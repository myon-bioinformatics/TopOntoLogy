"""OnTautoLogy graph procedures wired into classification, SIBLINGS, and pins."""
import json
import tempfile
import unittest
from pathlib import Path

import topontology
from test_series import load_mutant
from test_topontology import ROOT, run_cli

TIP = "a" * 40
MID = "b" * 40
PIN = "c" * 40
SIDE = "d" * 40
CHAIN = [
    {"sha": TIP, "parents": [MID, SIDE]},
    {"sha": MID, "parents": [PIN]},
    {"sha": PIN, "parents": []},
]
DIRECTIONS = {"similarTo": "mutual", "narrower": "forward"}


def _edge(source, target, relation="narrower", **extra):
    return {"source": source, "target": target, "kind": relation, **extra}


class CollapseTests(unittest.TestCase):
    def test_mutual_pair_is_the_otl_fold_and_the_dropped_edge_is_recorded(self):
        edges = [
            _edge("C", "B", "similarTo"),
            _edge("B", "C", "similarTo"),
            _edge("A", "B", "narrower"),
        ]
        kept, pairs = topontology._collapse_edges(edges, DIRECTIONS)
        direct = topontology.collapse_reciprocal(
            [topontology._otl_edge(edge) for edge in edges], DIRECTIONS)
        self.assertEqual(
            sorted((edge["source"], edge["target"], edge["kind"]) for edge in kept),
            sorted((edge["source"], edge["target"], edge["kind"]) for edge in direct["edges"]),
        )
        self.assertEqual(pairs, [{
            "relation": "similarTo",
            "dropped": {"source": "C", "target": "B"},
            "kept": {"source": "B", "target": "C"},
        }])
        report = topontology.summarize({"edges": edges}, {"similarTo": "similarity", "narrower": "hierarchy"},
                                        directions=DIRECTIONS)
        self.assertEqual(report["collapsed_edges"], pairs)
        self.assertEqual(report["classes"]["similarity"]["edges"], 1)
        self.assertEqual(report["classes"]["similarity"]["cycle_basis"]["independent_cycles"], 0)

    def test_skipping_the_fold_is_killed(self):
        mutant = load_mutant(
            "    folded = collapse_reciprocal(prepared, directions)\n",
            "    folded = {\"edges\": list(prepared)}\n",
        )
        edges = [_edge("B", "C", "similarTo"), _edge("C", "B", "similarTo")]
        _kept, pairs = topontology._collapse_edges(edges, DIRECTIONS)
        _other, missed = mutant._collapse_edges(edges, DIRECTIONS)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(missed, [])

    def test_non_strings_are_not_rewritten(self):
        edges = [_edge(1, "B"), _edge("", "B"), _edge("A", ""), _edge("A", "B", "")]
        self.assertIsNone(topontology._otl_edge(edges[0]))
        self.assertIsNone(topontology._otl_edge(edges[1]))
        self.assertIsNone(topontology._otl_edge(edges[2]))
        self.assertIsNone(topontology._otl_edge({"source": "A", "target": "B", "relation": 1}))
        kept, pairs = topontology._collapse_edges([_edge(1, 2)], DIRECTIONS)
        self.assertEqual(kept, [_edge(1, 2)])
        self.assertEqual(pairs, [])
        report = topontology.summarize({"edges": [_edge(1, "B")]}, {"narrower": "hierarchy"})
        self.assertEqual(report["classes"]["hierarchy"]["cycle_basis"]["status"], "not applicable")
        self.assertEqual(report["spof"]["status"], "not applicable")
        self.assertEqual(report["collapsed_edges"], [])

    def test_unknown_direction_is_rejected(self):
        with self.assertRaises(ValueError) as raised:
            topontology.summarize(
                {"edges": [_edge("A", "B")]},
                {"narrower": "hierarchy"},
                directions={"narrower": "sideways"},
            )
        self.assertIn("sideways", str(raised.exception))
        self.assertEqual(topontology._direction_map([]), {})
        self.assertEqual(topontology._direction_map({"demo": {}}), {})
        self.assertEqual(topontology._direction_map({"schema": "taxontoy-relations/2"}), {})
        with self.assertRaises(ValueError):
            topontology._direction_map({"schema": "taxontoy-relations/2", "direction": []})
        with self.assertRaises(ValueError):
            topontology._direction_map({
                "schema": "taxontoy-relations/2",
                "direction": {"": "forward"},
            })
        with self.assertRaises(ValueError):
            topontology._direction_map({
                "schema": "taxontoy-relations/2",
                "direction": {"narrower": ""},
            })


class BasisAndSpofTests(unittest.TestCase):
    def test_basis_matches_otl_and_a_dropped_basis_is_killed(self):
        edges = [_edge("A", "B"), _edge("B", "C"), _edge("A", "C")]
        report = topontology._cycle_basis_report(edges)
        direct = topontology.cycle_basis({"edges": [topontology._otl_edge(edge) for edge in edges]})
        self.assertEqual(report["independent_cycles"], direct["independent_cycles"])
        self.assertEqual(report["independent_cycles"], 1)
        self.assertEqual(report["cycles"][0]["labels"], direct["cycles"][0]["labels"])
        mutant = load_mutant(
            '    found = cycle_basis({"edges": prepared})\n',
            '    found = {"cycles": [], "independent_cycles": 0}\n',
        )
        self.assertEqual(mutant._cycle_basis_report(edges)["independent_cycles"], 0)
        empty = topontology._cycle_basis_report([])
        self.assertEqual(empty, {"cycles": [], "independent_cycles": 0})

    def test_sources_are_the_roots_and_an_empty_report_is_killed(self):
        edges = [_edge("A", "B"), _edge("B", "C")]
        report = topontology._spof_report(edges)
        direct = topontology.single_points_of_failure(
            {"edges": [topontology._otl_edge(edge) for edge in edges]}, ["A"])
        self.assertEqual(report["roots"], ["A"])
        self.assertEqual(report["articulation_points"], direct["articulation_points"])
        self.assertIn("B", report["articulation_points"])
        cycle = topontology._spof_report([_edge("A", "B"), _edge("B", "A")])
        self.assertEqual(cycle["reason"], "no source root")
        self.assertEqual(topontology._spof_report([])["reason"], "no edges")
        single = topontology._spof_report([_edge("A", "B")])
        self.assertEqual(single["articulation_points"], [])
        mutant = load_mutant(
            '    return single_points_of_failure({"edges": prepared}, roots)\n',
            '    return {"articulation_points": [], "dominators": [], "ranked": [], "roots": list(roots)}\n',
        )
        self.assertEqual(mutant._spof_report(edges)["articulation_points"], [])

    def test_text_renders_both_shapes(self):
        body = topontology.document(
            topontology.summarize(
                {"edges": [_edge("A", "B"), _edge("B", "A", "similarTo"), _edge("A", "B", "similarTo")]},
                {"narrower": "hierarchy", "similarTo": "similarity"},
                directions={"similarTo": "mutual"},
            ),
            {"graph": {"path": "g.json", "sha256": "ab"},
             "classification": {"path": "c.json", "sha256": "cd"},
             "profile": "demo"},
        )
        # The similarTo pair collapses, so the rendered summary has a pair and a source.
        text = topontology.render_text(body)
        self.assertIn("collapsed_edges: similarTo", text)
        self.assertIn("spof: roots=", text)
        self.assertIn("pin_lag: (none)", text)
        self.assertIn("cycle_basis:", text)
        only = topontology.summarize({"edges": [_edge("A", "B"), _edge("B", "A")]}, {"narrower": "hierarchy"})
        cycle_text = topontology.render_text(topontology.document(only, body["provenance"]))
        self.assertIn("spof: not applicable (no source root)", cycle_text)
        self.assertIn("collapsed_edges: (none)", cycle_text)
        mixed = topontology.summarize({"edges": [_edge(1, 2)]}, {"narrower": "hierarchy"})
        mixed_text = topontology.render_text(topontology.document(mixed, body["provenance"]))
        self.assertIn("cycle_basis: not applicable", mixed_text)
        self.assertIn("spof: not applicable", mixed_text)


class PinLagTests(unittest.TestCase):
    def test_lag_is_the_otl_count_and_a_swap_is_killed(self):
        edges = [_edge("local", "upstream", "vendorsFrom", pinned=PIN)]
        tagged = topontology._attach_pin_lag(edges, CHAIN)
        direct = topontology.first_parent_lag(CHAIN, PIN)
        self.assertEqual(tagged[0]["lag"], {"commits": direct["commits"], "merges": direct["merges"]})
        self.assertEqual(tagged[0]["lag"], {"commits": 2, "merges": 1})
        self.assertEqual(topontology._attach_pin_lag(edges, None), edges)
        self.assertIs(topontology._attach_pin_lag([_edge("A", "B")], CHAIN)[0].get("lag"), None)
        blank = topontology._attach_pin_lag([_edge("A", "B", pinned="  ")], CHAIN)
        self.assertNotIn("lag", blank[0])
        with self.assertRaises(ValueError):
            topontology._attach_pin_lag(edges, {"sha": PIN})
        with self.assertRaises(ValueError):
            topontology._attach_pin_lag(edges, [])
        rows = topontology._pin_lag_rows(tagged)
        self.assertEqual(rows[0]["commits"], 2)
        self.assertEqual(rows[0]["merges"], 1)
        mutant = load_mutant(
            '        copied["lag"] = {"commits": lag["commits"], "merges": lag["merges"]}\n',
            '        copied["lag"] = {"commits": lag["merges"], "merges": lag["commits"]}\n',
        )
        swapped = mutant._attach_pin_lag(edges, CHAIN)
        self.assertEqual(swapped[0]["lag"], {"commits": 1, "merges": 2})
        line = topontology._pin_lag_line(rows)
        self.assertIn("commits=2", line)
        self.assertIn("merges=1", line)

    def test_graph_and_vendor_lock_store_the_attribute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph = root / "graph.json"
            graph.write_text(json.dumps({
                "first_parent": CHAIN,
                "edges": [_edge("local", "upstream", "vendorsFrom", pinned=PIN)],
            }), encoding="utf-8")
            loaded = topontology._load_graph(graph)
            self.assertEqual(loaded["edges"][0]["lag"]["commits"], 2)
            absent = root / "plain.json"
            absent.write_text(json.dumps({"edges": [_edge("A", "B")], "first_parent": None}), encoding="utf-8")
            self.assertNotIn("lag", topontology._load_graph(absent)["edges"][0])
            lock = {
                "schema": "vendor-lock/1",
                "first_parent": CHAIN,
                "files": [{
                    "path": "copy.py",
                    "repository": "upstream",
                    "source": "orig.py",
                    "commit": PIN,
                }],
            }
            vendored = topontology.graph_from_vendor_lock(lock)
            self.assertEqual(vendored["edges"][0]["lag"]["merges"], 1)
            lock["files"][0]["commit"] = 5
            ignored = topontology.graph_from_vendor_lock(lock)
            self.assertNotIn("lag", ignored["edges"][0])
            lock["files"][0]["commit"] = "  "
            blank = topontology.graph_from_vendor_lock(lock)
            self.assertNotIn("pinned", blank["edges"][0])
            code, out, err = run_cli(
                str(graph), "tests/fixtures/vendor-classes.json", "demo", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("pin_lag:", out)
            self.assertIn("commits=2", out)
            code, out, err = run_cli(str(graph), "tests/fixtures/vendor-classes.json", "demo", "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["pin_lag"][0]["merges"], 1)


class SiblingsCoverTests(unittest.TestCase):
    def test_overlap_keeps_the_otl_cover_and_listing_everyone_is_killed(self):
        table = {"wide": ("p", "q"), "narrow": ("p",)}
        cover = topontology.siblings_cover(table)
        direct = topontology.set_cover_min(["p", "q"], {"wide": ["p", "q"], "narrow": ["p"]})
        self.assertEqual(cover, direct)
        self.assertEqual(cover["cover"], ["wide"])
        self.assertEqual(cover["unique"]["narrow"], [])
        self.assertEqual(cover["unique"]["wide"], ["q"])
        empty = topontology.siblings_cover({})
        self.assertEqual(empty["cover"], [])
        mutant = load_mutant(
            "    return set_cover_min(universe, candidates)\n",
            "    return {\"complete\": True, \"cover\": sorted(candidates),"
            " \"method\": \"exact\", \"size\": len(candidates), \"uncovered\": [],"
            " \"unique\": {name: [] for name in candidates}}\n",
        )
        self.assertEqual(mutant.siblings_cover(table)["cover"], ["narrow", "wide"])
        lines = "\n".join(topontology._cover_lines(cover))
        self.assertIn("siblings_cover: exact size=1 wide", lines)
        self.assertIn("unique narrow: (none)", lines)
        self.assertIn("unique wide: q", lines)
        self.assertIn("(none)", "\n".join(topontology._cover_lines(empty)))

    def test_cli_text_and_json_include_the_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "siblings.py"
            path.write_text('SIBLINGS = {"wide": ("p", "q"), "narrow": ("p",)}\n', encoding="utf-8")
            code, out, err = run_cli(
                str(path), "tests/fixtures/sibling-classes.json", "siblings",
                "--graph-format", "aoi-siblings", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("siblings_cover: exact size=1 wide", out)
            self.assertIn("unique narrow: (none)", out)
            code, out, err = run_cli(
                str(path), "tests/fixtures/sibling-classes.json", "siblings",
                "--graph-format", "aoi-siblings", "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["siblings_cover"]["cover"], ["wide"])


class PropertiesRoleTests(unittest.TestCase):
    def test_shapes_stay_distinct(self):
        self.assertEqual(topontology._properties_role(None), "metadata")
        self.assertEqual(topontology._properties_role({}), "metadata")
        self.assertEqual(topontology._properties_role([]), "metadata")
        self.assertEqual(topontology._properties_role({"narrower": "hierarchy"}), "profile")
        self.assertEqual(topontology._properties_role({"classes": {"hierarchy": {}}}), "metadata")
        self.assertEqual(topontology._properties_role({"classes": ["hierarchy"]}), "mixed")
        self.assertEqual(topontology._properties_role({"": "hierarchy"}), "mixed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(json.dumps({
                "schema": "taxontoy-relations/2",
                "concepts": {"narrower": "hierarchy"},
                "properties": {},
            }), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(path, "properties")
            self.assertEqual(str(raised.exception), "unknown profile: properties")
            path.write_text(json.dumps({
                "schema": "taxontoy-relations/2",
                "concepts": {"narrower": "hierarchy"},
                "properties": [],
            }), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(path, "properties")
            self.assertEqual(str(raised.exception), "unknown profile: properties")

    def test_calling_mixed_metadata_is_killed(self):
        mutant = load_mutant(
            '    return "mixed"\n',
            '    return "metadata"\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(json.dumps({
                "schema": "taxontoy-relations/2",
                "properties": {"classes": ["hierarchy"]},
            }), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(path, "properties")
            self.assertIn("mixes", str(raised.exception))
            with self.assertRaises(ValueError) as raised:
                mutant._load_classes(path, "properties")
            self.assertEqual(str(raised.exception), "unknown profile: properties")


class OtlImportTests(unittest.TestCase):
    def test_missing_names_are_one_line_on_the_cli_and_reraise_off_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ontautology_graphs.py").write_text("", encoding="utf-8")
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--otl-root", str(root),
            )
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertNotIn("Traceback", err)
            self.assertEqual(err.count("\n"), 1)
            self.assertIn("collapse_reciprocal", err)
            code, out, err = run_cli("--otl-root")
            self.assertEqual(code, 1)
            self.assertIn("OTL_ROOT", err)
            self.assertNotIn("Traceback", err)
        import sys
        import types
        saved = sys.modules.get("ontautology_graphs")
        sys.modules["ontautology_graphs"] = types.ModuleType("ontautology_graphs")
        try:
            with self.assertRaises(ImportError):
                topontology._load_otl_api()
        finally:
            if saved is None:
                sys.modules.pop("ontautology_graphs", None)
            else:
                sys.modules["ontautology_graphs"] = saved


if __name__ == "__main__":
    unittest.main()
