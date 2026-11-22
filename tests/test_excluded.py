"""Excluded-relation impact, including the depends_on alias and source mutants."""
import json
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_series import load_mutant
from test_topontology import ROOT, run_cli

GRAPH = ROOT / "tests/fixtures/excluded-graph.json"
TABLE = ROOT / "tests/fixtures/excluded-relations.json"


def _edge(source, relation, target):
    return {"source": source, "kind": relation, "target": target}


def _load():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    classes = table["audit"]
    return graph, classes, table


def _impact(relation, category, report):
    row = next(item for item in report["relations"] if item["relation"] == relation)
    return next(item for item in row["impacts"] if item["class"] == category)


class ExcludedImpactTests(unittest.TestCase):
    def test_parallel_alias_does_not_pass_acyclicity_but_the_inverse_does(self):
        graph, classes, table = _load()
        report = topontology.excluded_impact(graph, classes, table, "audit")
        self.assertEqual(report["excluded"], ["belongsTo", "blocks", "depends_on", "overlapsWith"])
        dependency = report["classes"]["dependency"]
        # The whole excluded set includes blocks, so the class fails acyclicity until they are left out.
        self.assertTrue(dependency["acyclic_declared"])
        self.assertTrue(dependency["exclusion_passes_acyclicity"])
        self.assertTrue(dependency["removed_cycles"])
        self.assertTrue(dependency["removed_directed_cycle"])
        self.assertFalse(dependency["split_components"])
        self.assertIsNone(dependency["with"]["depth"])
        self.assertEqual(dependency["without"]["depth"], 2)
        self.assertEqual(dependency["without"]["independent_cycles"], 0)
        self.assertGreater(dependency["with"]["edges"], dependency["without"]["edges"])
        self.assertLess(dependency["delta"]["edges"], 0)
        overlap = report["classes"]["overlap"]
        self.assertFalse(overlap["acyclic_declared"])
        self.assertFalse(overlap["exclusion_passes_acyclicity"])
        depends = next(item for item in report["relations"] if item["relation"] == "depends_on")
        self.assertEqual(depends["alias_of"], ["dependsOn"])
        self.assertEqual(depends["edges"], 2)
        folded = _impact("depends_on", "dependency", report)
        # Same directed pairs as dependsOn: two extra edges, no directed cycle.
        self.assertEqual(folded["with"]["edges"], 4)
        self.assertEqual(folded["with"]["independent_cycles"], 2)
        self.assertEqual(folded["without"]["independent_cycles"], 0)
        self.assertIsNone(folded["with"]["order_cycle"])
        self.assertTrue(folded["removed_cycles"])
        self.assertFalse(folded["removed_directed_cycle"])
        self.assertFalse(folded["split_components"])
        self.assertFalse(folded["exclusion_passes_acyclicity"])
        self.assertEqual(folded["delta"]["independent_cycles"], -2)
        self.assertEqual(folded["delta"]["weakly_connected_components"], 0)
        self.assertEqual(folded["delta"]["depth"], 0)
        blocks = next(item for item in report["relations"] if item["relation"] == "blocks")
        self.assertEqual(blocks["alias_of"], [])
        inverse = _impact("blocks", "dependency", report)
        self.assertIsNotNone(inverse["with"]["order_cycle"])
        self.assertIsNone(inverse["without"]["order_cycle"])
        self.assertTrue(inverse["removed_directed_cycle"])
        self.assertTrue(inverse["exclusion_passes_acyclicity"])
        self.assertFalse(inverse["split_components"])
        bridge = _impact("belongsTo", "overlap", report)
        # Q→R joins {P,Q} and {R,S}. Excluding it splits that class back into two components.
        self.assertEqual(bridge["with"]["weakly_connected_components"]["count"], 1)
        self.assertEqual(bridge["without"]["weakly_connected_components"]["count"], 2)
        self.assertTrue(bridge["split_components"])
        self.assertFalse(bridge["removed_cycles"])
        self.assertFalse(bridge["exclusion_passes_acyclicity"])
        self.assertEqual(bridge["delta"]["weakly_connected_components"], 1)
        parallel = _impact("overlapsWith", "overlap", report)
        self.assertTrue(parallel["removed_cycles"])
        self.assertFalse(parallel["removed_directed_cycle"])
        self.assertFalse(parallel["split_components"])
        self.assertEqual(
            report["acyclicity_flags"],
            [{"class": "dependency", "relation": "blocks"}],
        )
        # Excluded edges leave the unclassified bucket when they are folded in.
        self.assertEqual(report["unclassified_edges"]["without"], 6)
        self.assertEqual(report["unclassified_edges"]["with"], 0)
        self.assertEqual(report["unclassified_edges"]["delta"], 6)

    def test_two_relations_cycle_only_together_and_undeclared_class(self):
        classes = {"dependsOn": "dependency", "duplicates": "overlap"}
        table = {
            "schema": "taxontoy-relations/2",
            "audit": classes,
            "excluded": {"audit": ["step_x", "step_y", "echo"]},
            "properties": {"classes": {"dependency": {"acyclic": {"since": "2026-11-14", "reason": "x"}}}},
        }
        graph = {"edges": [
            _edge("A", "dependsOn", "B"),
            _edge("B", "step_x", "X"),
            _edge("X", "step_y", "A"),
            _edge("P", "duplicates", "Q"),
            _edge("Q", "echo", "P"),
        ]}
        report = topontology.excluded_impact(graph, classes, table, "audit")
        self.assertTrue(report["classes"]["dependency"]["exclusion_passes_acyclicity"])
        self.assertFalse(_impact("step_x", "dependency", report)["exclusion_passes_acyclicity"])
        self.assertFalse(_impact("step_y", "dependency", report)["exclusion_passes_acyclicity"])
        self.assertEqual(report["acyclicity_flags"], [])
        echo = _impact("echo", "overlap", report)
        self.assertTrue(echo["removed_directed_cycle"])
        self.assertFalse(echo["acyclic_declared"])
        self.assertFalse(echo["exclusion_passes_acyclicity"])

    def test_already_cyclic_class_is_not_saved_by_exclusion(self):
        classes = {"narrower": "hierarchy"}
        table = {
            "schema": "taxontoy-relations/2",
            "concepts": classes,
            "excluded": {"concepts": ["extra"]},
            "properties": {"classes": {"hierarchy": {"acyclic": {"since": "2026-11-15", "reason": "x"}}}},
        }
        graph = {"edges": [
            _edge("A", "narrower", "B"),
            _edge("B", "narrower", "A"),
            _edge("A", "extra", "B"),
        ]}
        report = topontology.excluded_impact(graph, classes, table, "concepts")
        row = _impact("extra", "hierarchy", report)
        self.assertIsNotNone(row["without"]["order_cycle"])
        self.assertFalse(row["exclusion_passes_acyclicity"])
        self.assertTrue(row["removed_cycles"])
        self.assertFalse(row["removed_directed_cycle"])

    def test_alias_forms_and_ambiguous_targets(self):
        classes = {"dependsOn": "dependency", "depends-on": "overlap"}
        self.assertEqual(topontology._separator_key("depends_on"), ("depends", "on"))
        self.assertEqual(topontology._separator_key("depends-on"), ("depends", "on"))
        self.assertEqual(topontology._separator_key("depends on"), ("depends", "on"))
        self.assertEqual(topontology._separator_key("DependsOn"), ("depends", "on"))
        self.assertEqual(topontology._separator_key("depends__on"), ("depends", "on"))
        self.assertEqual(topontology._separator_key("depends_"), ("depends",))
        self.assertEqual(topontology._separator_key(""), ())
        self.assertEqual(topontology._alias_of("depends_on", {"dependsOn": "dependency"}), ["dependsOn"])
        self.assertEqual(topontology._alias_of("blocks", {"dependsOn": "dependency"}), [])
        self.assertEqual(
            topontology._alias_of("depends_on", classes),
            ["depends-on", "dependsOn"],
        )
        table = {
            "schema": "taxontoy-relations/1",
            "audit": classes,
            "excluded": {"audit": ["depends_on"]},
        }
        report = topontology.excluded_impact(
            {"edges": [_edge("A", "depends_on", "B")]}, classes, table, "audit")
        self.assertEqual(report["relations"][0]["alias_of"], ["depends-on", "dependsOn"])
        self.assertEqual(topontology._excluded_names(
            json.loads((ROOT / "tests/fixtures/relations-v1.json").read_text(encoding="utf-8")),
            "audit",
            {"dependsOn": "dependency"},
        ), ["hasName"])

    def test_malformed_excluded_lists_and_empty_profile(self):
        classes = {"dependsOn": "dependency"}
        with self.assertRaises(ValueError) as raised:
            topontology.excluded_impact({"edges": []}, classes, ["nope"], "audit")
        self.assertIn("classification JSON must be an object", str(raised.exception))
        base = {"schema": "taxontoy-relations/2", "audit": classes}
        with self.assertRaises(ValueError):
            topontology._excluded_names({**base, "excluded": []}, "audit", classes)
        with self.assertRaises(ValueError) as raised:
            topontology._excluded_names({**base, "excluded": {"concepts": []}}, "audit", classes)
        self.assertIn("missing for profile", str(raised.exception))
        with self.assertRaises(ValueError):
            topontology._excluded_names({**base, "excluded": {"audit": "depends_on"}}, "audit", classes)
        with self.assertRaises(ValueError):
            topontology._excluded_names({**base, "excluded": {"audit": [""]}}, "audit", classes)
        with self.assertRaises(ValueError):
            topontology._excluded_names({**base, "excluded": {"audit": [" x"]}}, "audit", classes)
        with self.assertRaises(ValueError):
            topontology._excluded_names({**base, "excluded": {"audit": [1]}}, "audit", classes)
        with self.assertRaises(ValueError) as raised:
            topontology._excluded_names({**base, "excluded": {"audit": ["blocks", "blocks"]}}, "audit", classes)
        self.assertIn("duplicate", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            topontology._excluded_names({**base, "excluded": {"audit": ["dependsOn"]}}, "audit", classes)
        self.assertIn("both classified and excluded", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            topontology._excluded_names({"schema": "taxontoy-relations/3", "audit": classes}, "audit", classes)
        self.assertIn("taxontoy-relations/3", str(raised.exception))
        self.assertEqual(topontology._excluded_names({"demo": classes}, "demo", classes), [])
        empty = topontology.excluded_impact(
            {"edges": [_edge("A", "belongsTo", "B")]},
            {},
            {"schema": "taxontoy-relations/2", "audit": {}, "excluded": {"audit": ["belongsTo"]}},
            "audit",
        )
        self.assertEqual(empty["classes"], {})
        self.assertEqual(empty["relations"][0]["impacts"], [])
        self.assertEqual(empty["relations"][0]["edges"], 1)
        self.assertEqual(empty["acyclicity_flags"], [])
        self.assertFalse(topontology._class_declares_acyclic({}, "dependency"))
        self.assertFalse(topontology._class_declares_acyclic({"properties": []}, "dependency"))
        self.assertFalse(topontology._class_declares_acyclic({"properties": {"classes": []}}, "dependency"))
        self.assertFalse(topontology._class_declares_acyclic({"properties": {"classes": {}}}, "dependency"))
        self.assertFalse(topontology._class_declares_acyclic(
            {"properties": {"classes": {"dependency": []}}}, "dependency"))
        self.assertFalse(topontology._class_declares_acyclic(
            {"properties": {"classes": {"dependency": {"symmetric": {}}}}}, "dependency"))
        self.assertTrue(topontology._class_declares_acyclic(
            {"properties": {"classes": {"dependency": {"acyclic": {}}}}}, "dependency"))
        none = topontology.excluded_impact(
            {"edges": [_edge("A", "dependsOn", "B")]},
            classes,
            {"schema": "taxontoy-relations/2", "audit": classes, "excluded": {"audit": []},
             "properties": {"classes": {"dependency": {"acyclic": {}}}}},
            "audit",
        )
        self.assertEqual(none["excluded"], [])
        self.assertEqual(none["relations"], [])
        self.assertFalse(none["classes"]["dependency"]["exclusion_passes_acyclicity"])
        self.assertEqual(none["classes"]["dependency"]["delta"]["edges"], 0)


class ExcludedCliTests(unittest.TestCase):
    def test_canonical_output_and_text_flags(self):
        code, out, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/excluded-relations.json", "audit",
            "--format", "canonical",
        )
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/excluded.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(body["tool"]["version"], topontology.__version__)
        self.assertEqual(body["provenance"]["profile"], "audit")
        self.assertEqual(body["acyclicity_flags"], [{"class": "dependency", "relation": "blocks"}])
        code, text, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/excluded-relations.json", "audit",
            "--format", "text",
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"tool: topontology {topontology.__version__}", text)
        self.assertIn("excluded: belongsTo, blocks, depends_on, overlapsWith", text)
        self.assertIn("acyclicity_flags: blocks dependency", text)
        self.assertIn("relation depends_on edges=2 alias_of=dependsOn", text)
        self.assertIn("exclusion_passes_acyclicity=no", text)
        self.assertIn("exclusion_passes_acyclicity=yes", text)
        self.assertIn("split_components=yes", text)
        code, sugar, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/excluded-relations.json", "audit",
            "--json",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(sugar, out)
        code, out, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/excluded-relations.json", "audit",
            "--json", "--format", "text",
        )
        self.assertEqual(code, 1)
        self.assertIn("--json", err)
        self.assertNotIn("Traceback", err)

    def test_env_override_empty_profile_and_vendor_lock(self):
        code, out, err = run_cli(
            "excluded",
            "tests/fixtures/excluded-graph.json",
            "tests/fixtures/excluded-relations.json",
            "cases",
            "--format", "json",
            env={
                "TOPONTOLOGY_GRAPH": "tests/fixtures/graph.json",
                "TOPONTOLOGY_CLASSIFICATION": "tests/fixtures/classes.json",
                "TOPONTOLOGY_PROFILE": "audit",
            },
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["excluded"], [])
        self.assertEqual(body["relations"], [])
        self.assertEqual(body["acyclicity_flags"], [])
        self.assertIn("hypothesis", body["classes"])
        code, text, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/excluded-relations.json", "cases",
            "--format", "text",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("excluded: (none)", text)
        self.assertIn("acyclicity_flags: (none)", text)
        self.assertIn("relations: (none)", text)
        code, out, err = run_cli(
            "excluded", "tests/fixtures/graph.json", "tests/fixtures/excluded-relations.json", "audit",
            env={"TOPONTOLOGY_GRAPH": "tests/fixtures/excluded-graph.json"},
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["provenance"]["graph"]["path"], "tests/fixtures/graph.json")
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "empty.json"
            graph = Path(tmp) / "graph.json"
            dest = Path(tmp) / "out.json"
            table.write_text(json.dumps({
                "schema": "taxontoy-relations/2",
                "audit": {},
                "excluded": {"audit": ["belongsTo"]},
            }), encoding="utf-8")
            graph.write_text(json.dumps({"edges": []}), encoding="utf-8")
            code, out, err = run_cli("excluded", str(graph), str(table), "audit", "--format", "text", "-o", str(dest))
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            written = dest.read_text(encoding="utf-8")
            self.assertIn("classes: (none)", written)
            self.assertIn("relation belongsTo edges=0 alias_of=(none)", written)
            self.assertIn("  (no class)", written)
            code, out, err = run_cli(
                "excluded", str(graph), str(table), "audit", "--cycle-limit", "-1")
            self.assertEqual(code, 1)
            self.assertIn("--cycle-limit", err)
            self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "excluded",
            "tests/fixtures/aoi-history/c8184924634965e38c89420c61ef8f3c14a6e2ff.json",
            "tests/fixtures/vendor-classes.json",
            "demo",
            "--graph-format", "vendor-lock",
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["excluded"], [])
        self.assertGreater(body["classes"]["dependency"]["without"]["edges"], 0)
        self.assertEqual(body["classes"]["dependency"]["delta"]["edges"], 0)
        code, out, err = run_cli(
            "excluded", "tests/fixtures/excluded-graph.json", "tests/fixtures/relations-v3.json", "audit")
        self.assertEqual(code, 1)
        self.assertIn("unknown relation schema", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli("excluded", env={"TOPONTOLOGY_PROFILE": "audit"})
        self.assertEqual(code, 1)
        self.assertIn("pass the graph path", err)
        self.assertNotIn("TOPONTOLOGY_PROFILE", err)
        self.assertNotIn("Traceback", err)


class ExcludedMutationTests(unittest.TestCase):
    def test_swapped_with_and_without_is_killed(self):
        mutant = load_mutant(
            "compared = compare_graphs(with_report, without_report)",
            "compared = compare_graphs(without_report, with_report)",
        )
        graph, classes, table = _load()
        real = topontology.excluded_impact(graph, classes, table, "audit")
        changed = mutant.excluded_impact(graph, classes, table, "audit")
        self.assertGreater(
            real["classes"]["dependency"]["with"]["edges"],
            real["classes"]["dependency"]["without"]["edges"],
        )
        self.assertLess(
            changed["classes"]["dependency"]["with"]["edges"],
            changed["classes"]["dependency"]["without"]["edges"],
        )

    def test_underscore_alias_miss_is_killed(self):
        mutant = load_mutant('if char in "_- ":', 'if char in "- ":')
        classes = {"dependsOn": "dependency"}
        self.assertEqual(topontology._alias_of("depends_on", classes), ["dependsOn"])
        self.assertEqual(mutant._alias_of("depends_on", classes), [])
        self.assertEqual(mutant._alias_of("depends-on", classes), ["dependsOn"])

    def test_forced_acyclicity_flag_off_is_killed(self):
        mutant = load_mutant(
            "if acyclic_declared and with_metrics[\"order_cycle\"] is not None and without_metrics[\"order_cycle\"] is None:",
            "if False and acyclic_declared and with_metrics[\"order_cycle\"] is not None and without_metrics[\"order_cycle\"] is None:",
        )
        graph, classes, table = _load()
        real = topontology.excluded_impact(graph, classes, table, "audit")
        changed = mutant.excluded_impact(graph, classes, table, "audit")
        self.assertEqual(real["acyclicity_flags"], [{"class": "dependency", "relation": "blocks"}])
        self.assertEqual(changed["acyclicity_flags"], [])


if __name__ == "__main__":
    unittest.main()
