"""Declared property witnesses and regressions. Graph searches stay in OnTautoLogy."""
import json
import tempfile
import unittest
from pathlib import Path

import topontology
from test_series import load_mutant
from test_topontology import run_cli


def _edge(source, target, relation):
    return {"source": source, "target": target, "kind": relation}


def _write(directory, name, payload):
    path = Path(directory) / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _table(**properties):
    table = {
        "schema": "taxontoy-relations/2",
        "concepts": {"narrower": "hierarchy", "similarTo": "similarity", "blocks": "opposition"},
        "direction": {"narrower": "forward", "similarTo": "mutual", "blocks": "forward"},
        "excluded": {"concepts": []},
    }
    if properties:
        table["properties"] = properties
    return table


class PropertyEvaluationTests(unittest.TestCase):
    def test_acyclic_witness_is_the_otl_cycle(self):
        edges = [
            _edge("A", "C", "narrower"),
            _edge("B", "C", "narrower"),
            _edge("B", "A", "narrower"),
            _edge("C", "A", "narrower"),
        ]
        graph = {"edges": edges}
        classes = {"narrower": "hierarchy"}
        table = {"properties": {"classes": {"hierarchy": {"acyclic": {"since": "2026-11-15"}}}}}
        rows = topontology.evaluate_properties(graph, classes, table)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["holds"])
        direct = topontology.directed_cycle_witness({"edges": [topontology._otl_edge(edge) for edge in edges]})
        self.assertEqual(rows[0]["witness"]["nodes"], direct["nodes"])
        self.assertEqual(
            [(edge["source"], edge["target"]) for edge in rows[0]["witness"]["edges"]],
            [(edge["source"], edge["target"]) for edge in direct["edges"]],
        )

    def test_dropping_the_cycle_witness_is_killed(self):
        mutant = load_mutant(
            '    found = directed_cycle_witness({"edges": prepared})\n',
            "    found = None\n",
        )
        graph = {"edges": [_edge("A", "A", "narrower")]}
        table = {"properties": {"classes": {"hierarchy": {"acyclic": {}}}}}
        self.assertFalse(topontology.evaluate_properties(graph, {"narrower": "hierarchy"}, table)[0]["holds"])
        self.assertTrue(mutant.evaluate_properties(graph, {"narrower": "hierarchy"}, table)[0]["holds"])

    def test_transitive_witness_is_the_otl_path(self):
        edges = [_edge("A", "B", "narrower"), _edge("B", "C", "narrower")]
        rows = topontology.evaluate_properties(
            {"edges": edges},
            {"narrower": "hierarchy"},
            {"properties": {"relations": {"narrower": {"transitive": {}}}}},
        )
        direct = topontology.transitivity_witness([topontology._otl_edge(edge) for edge in edges])
        self.assertEqual(rows[0]["witness"]["nodes"], direct["nodes"])
        self.assertEqual(rows[0]["witness"]["missing"], direct["missing"])
        held = edges + [_edge("A", "C", "narrower")]
        ok = topontology.evaluate_properties(
            {"edges": held},
            {"narrower": "hierarchy"},
            {"properties": {"relations": {"narrower": {"transitive": {}}}}},
        )
        self.assertTrue(ok[0]["holds"])
        self.assertIsNone(ok[0]["witness"])

    def test_dropping_the_transitive_witness_is_killed(self):
        mutant = load_mutant(
            "    found = transitivity_witness(prepared)\n",
            "    found = None\n",
        )
        graph = {"edges": [_edge("A", "B", "narrower"), _edge("B", "C", "narrower")]}
        table = {"properties": {"relations": {"narrower": {"transitive": {}}}}}
        classes = {"narrower": "hierarchy"}
        self.assertFalse(topontology.evaluate_properties(graph, classes, table)[0]["holds"])
        self.assertTrue(mutant.evaluate_properties(graph, classes, table)[0]["holds"])

    def test_set_checks_emit_one_counterexample(self):
        graph = {"edges": [
            _edge("A", "B", "similarTo"),
            _edge("A", "A", "similarTo"),
            _edge("A", "B", "blocks"),
            _edge("B", "C", "narrower"),
        ]}
        table = {"properties": {
            "classes": {"similarity": {"symmetric": {}}},
            "relations": {
                "blocks": {"inverse_of": {"relation": "dependsOn"}},
                "narrower": {"disjoint_with": [{"relation": "similarTo"}]},
            },
        }}
        classes = {"similarTo": "similarity", "blocks": "opposition", "narrower": "hierarchy"}
        rows = { (row["property"], row["name"]): row for row in topontology.evaluate_properties(graph, classes, table) }
        symmetric = rows[("symmetric", "similarity")]
        self.assertFalse(symmetric["holds"])
        self.assertEqual(symmetric["witness"]["edges"], [
            {"source": "A", "relation": "similarTo", "target": "B"},
        ])
        inverse = rows[("inverse_of", "blocks")]
        self.assertEqual(inverse["partner"], "dependsOn")
        self.assertEqual(inverse["witness"]["edges"][0]["relation"], "blocks")
        disjoint = rows[("disjoint_with", "narrower")]
        self.assertEqual(disjoint["witness"]["nodes"], ["B"])
        self.assertEqual(len(disjoint["witness"]["edges"]), 2)
        mirrored = {"edges": [_edge("A", "B", "similarTo"), _edge("B", "A", "similarTo")]}
        held = topontology.evaluate_properties(
            mirrored, {"similarTo": "similarity"},
            {"properties": {"classes": {"similarity": {"symmetric": {}}}}},
        )
        self.assertTrue(held[0]["holds"])
        inverse_held = topontology.evaluate_properties(
            {"edges": [_edge("A", "B", "blocks"), _edge("B", "A", "dependsOn")]},
            {"blocks": "opposition", "dependsOn": "dependency"},
            {"properties": {"relations": {"blocks": {"inverse_of": {"relation": "dependsOn"}}}}},
        )
        self.assertTrue(inverse_held[0]["holds"])
        apart = topontology.evaluate_properties(
            {"edges": [_edge("A", "B", "narrower"), _edge("C", "D", "similarTo")]},
            {"narrower": "hierarchy", "similarTo": "similarity"},
            {"properties": {"relations": {"narrower": {"disjoint_with": [{"relation": "similarTo"}]}}}},
        )
        self.assertTrue(apart[0]["holds"])

    def test_ignoring_a_missing_reverse_is_killed(self):
        mutant = load_mutant(
            "        if source != target and (target, source) not in pairs:\n",
            "        if False and source != target and (target, source) not in pairs:\n",
        )
        graph = {"edges": [_edge("A", "B", "similarTo")]}
        table = {"properties": {"relations": {"similarTo": {"symmetric": {}}}}}
        classes = {"similarTo": "similarity"}
        self.assertFalse(topontology.evaluate_properties(graph, classes, table)[0]["holds"])
        self.assertTrue(mutant.evaluate_properties(graph, classes, table)[0]["holds"])

    def test_ignoring_the_partner_side_of_inverse_is_killed(self):
        mutant = load_mutant(
            "        if (target, source) not in subject_pairs:\n",
            "        if False and (target, source) not in subject_pairs:\n",
        )
        graph = {"edges": [_edge("B", "A", "dependsOn")]}
        table = {"properties": {"relations": {"blocks": {"inverse_of": {"relation": "dependsOn"}}}}}
        classes = {"blocks": "opposition", "dependsOn": "dependency"}
        self.assertFalse(topontology.evaluate_properties(graph, classes, table)[0]["holds"])
        self.assertTrue(mutant.evaluate_properties(graph, classes, table)[0]["holds"])

    def test_ignoring_a_shared_endpoint_is_killed(self):
        mutant = load_mutant(
            "    shared = set(left) & set(right)\n",
            "    shared = set()\n",
        )
        graph = {"edges": [_edge("A", "B", "narrower"), _edge("B", "C", "similarTo")]}
        table = {"properties": {"relations": {"narrower": {"disjoint_with": [{"relation": "similarTo"}]}}}}
        classes = {"narrower": "hierarchy", "similarTo": "similarity"}
        self.assertFalse(topontology.evaluate_properties(graph, classes, table)[0]["holds"])
        self.assertTrue(mutant.evaluate_properties(graph, classes, table)[0]["holds"])

    def test_non_strings_do_not_call_otl_and_declarations_skip_other_profiles(self):
        graph = {"edges": [_edge(1, 2, "narrower")]}
        table = {
            "properties": {
                "classes": {
                    "hierarchy": {"acyclic": {}, "note": "user"},
                    "dependency": {"acyclic": {}},
                    "skipped": "not-a-body",
                },
                "relations": {"narrower": {"transitive": {}, "symmetric": {}}},
            },
        }
        classes = {"narrower": "hierarchy"}
        rows = topontology.evaluate_properties(graph, classes, table)
        by_property = {(row["scope"], row["property"]): row for row in rows}
        self.assertEqual(set(by_property), {("class", "acyclic"), ("relation", "transitive"), ("relation", "symmetric")})
        self.assertEqual(by_property[("class", "acyclic")]["status"], "not applicable")
        self.assertIsNone(by_property[("class", "acyclic")]["holds"])
        self.assertFalse(by_property[("relation", "symmetric")]["holds"])
        self.assertEqual(topontology._declaration_rows(None, classes), [])
        self.assertEqual(topontology._declaration_rows({"properties": "user"}, classes), [])
        self.assertEqual(
            topontology._declaration_rows({"properties": {"narrower": "hierarchy"}}, classes), [])
        self.assertEqual(
            topontology._declaration_rows({"properties": {"classes": []}}, classes), [])
        self.assertEqual(
            topontology._declaration_rows({"properties": {"relations": []}}, classes), [])
        self.assertEqual(topontology._declaration_rows({
            "properties": {"classes": {"hierarchy": "nope"}, "relations": {"narrower": "nope"}},
        }, classes), [])

    def test_bad_declarations_are_rejected(self):
        with self.assertRaises(ValueError):
            topontology._declaration_rows(
                {"properties": {"relations": {"blocks": {"inverse_of": "dependsOn"}}}},
                {"blocks": "opposition"},
            )
        with self.assertRaises(ValueError):
            topontology._declaration_rows(
                {"properties": {"relations": {"blocks": {"inverse_of": {"relation": ""}}}}},
                {"blocks": "opposition"},
            )
        with self.assertRaises(ValueError):
            topontology._declaration_rows(
                {"properties": {"relations": {"narrower": {"disjoint_with": []}}}},
                {"narrower": "hierarchy"},
            )
        with self.assertRaises(ValueError):
            topontology._properties_from_report({"tool": {}})


class RegressionTests(unittest.TestCase):
    def test_holding_to_failing_and_the_reverse(self):
        table = {"properties": {"classes": {
            "hierarchy": {"acyclic": {}},
            "similarity": {"symmetric": {}},
        }}}
        classes = {"narrower": "hierarchy", "similarTo": "similarity"}
        before = topontology.evaluate_properties(
            {"edges": [_edge("A", "B", "narrower"), _edge("A", "B", "similarTo")]},
            classes, table,
        )
        after = topontology.evaluate_properties(
            {"edges": [
                _edge("A", "B", "narrower"), _edge("B", "A", "narrower"),
                _edge("A", "B", "similarTo"), _edge("B", "A", "similarTo"),
            ]},
            classes, table,
        )
        change = topontology.property_regression(after, before)
        self.assertEqual([row["name"] for row in change["regressed"]], ["hierarchy"])
        self.assertEqual([row["name"] for row in change["recovered"]], ["similarity"])
        self.assertIsNotNone(change["regressed"][0]["witness"])
        skipped = topontology.property_regression(
            [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": None}],
            [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": True}],
        )
        self.assertEqual(skipped, {"recovered": [], "regressed": []})
        inverse = topontology.property_regression(
            [{"scope": "relation", "name": "blocks", "property": "inverse_of",
              "partner": "dependsOn", "holds": False, "witness": {"nodes": ["A"], "edges": []}}],
            [{"scope": "relation", "name": "blocks", "property": "inverse_of",
              "partner": "dependsOn", "holds": True, "witness": None}],
        )
        self.assertEqual(inverse["regressed"][0]["partner"], "dependsOn")
        absent = topontology.property_regression(
            [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": False}],
            [],
        )
        unknown = topontology.property_regression(
            [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": False}],
            [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": None}],
        )
        self.assertEqual(absent["regressed"], [])
        self.assertEqual(unknown["regressed"], [])
        text = topontology._properties_text(topontology.document(
            {"properties": [{
                "scope": "class", "name": "hierarchy", "property": "acyclic",
                "holds": None, "status": "not applicable", "witness": None,
                "reason": topontology._NOT_APPLICABLE,
            }], "regression": {
                "regressed": [{
                    "scope": "relation", "name": "blocks", "property": "inverse_of",
                    "partner": "dependsOn", "before": True, "after": False, "witness": None,
                }, {
                    "scope": "class", "name": "hierarchy", "property": "acyclic",
                    "before": True, "after": False, "witness": None,
                }],
                "recovered": [{
                    "scope": "relation", "name": "blocks", "property": "inverse_of",
                    "partner": "dependsOn", "before": False, "after": True, "witness": None,
                }],
            }},
            {"graph": {"path": "g.json", "sha256": "aa"},
             "classification": {"path": "c.json", "sha256": "bb"},
             "profile": "concepts",
             "baseline": {"path": "b.json", "sha256": "cc"}},
        ))
        self.assertIn("not applicable", text)
        self.assertIn("regressed: relation blocks inverse_of dependsOn, class hierarchy acyclic", text)
        self.assertIn("recovered: relation blocks inverse_of dependsOn", text)

    def test_hiding_a_regression_is_killed(self):
        mutant = load_mutant(
            '        if prior["holds"] and not row["holds"]:\n',
            '        if False and prior["holds"] and not row["holds"]:\n',
        )
        current = [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": False, "witness": {"nodes": ["A"]}}]
        baseline = [{"scope": "class", "name": "hierarchy", "property": "acyclic", "holds": True, "witness": None}]
        self.assertEqual(len(topontology.property_regression(current, baseline)["regressed"]), 1)
        self.assertEqual(mutant.property_regression(current, baseline)["regressed"], [])

    def test_fail_on_regression_exits_only_when_something_regressed(self):
        self.assertEqual(topontology._regression_status({"regression": {"regressed": []}}, True), 0)
        self.assertEqual(topontology._regression_status({"regression": {"regressed": [{}]}}, False), 0)
        self.assertEqual(topontology._regression_status({"regression": {"regressed": [{}]}}, True), 1)
        mutant = load_mutant(
            '    if fail and report["regression"]["regressed"]:\n        return 1\n',
            '    if fail and report["regression"]["regressed"]:\n        return 0\n',
        )
        self.assertEqual(mutant._regression_status({"regression": {"regressed": [{}]}}, True), 0)


class PropertiesCliTests(unittest.TestCase):
    def _files(self, tmp, graph, table):
        root = Path(tmp)
        return (
            _write(root, "graph.json", {"edges": graph}),
            _write(root, "table.json", table),
        )

    def test_text_json_and_mermaid(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, table = self._files(tmp, [
                _edge("A", "B", "narrower"),
                _edge("B", "A", "narrower"),
                _edge("B", "C", "narrower"),
                _edge("A", "B", "similarTo"),
                _edge("P", "Q", "blocks"),
            ], _table(
                classes={"hierarchy": {"acyclic": {}}, "similarity": {"symmetric": {}}},
                relations={
                    "narrower": {"transitive": {}},
                    "blocks": {"inverse_of": {"relation": "dependsOn"}},
                    "similarTo": {"disjoint_with": [{"relation": "narrower"}]},
                },
            ))
            code, out, err = run_cli("properties", str(graph), str(table), "concepts", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("property class hierarchy acyclic: fails", out)
            self.assertIn("nodes: A → B → A", out)
            self.assertIn("property class similarity symmetric: fails", out)
            self.assertIn("property relation narrower transitive: fails", out)
            self.assertIn("missing: A → C", out)
            self.assertIn("property relation blocks inverse_of dependsOn: fails", out)
            self.assertIn("property relation similarTo disjoint_with narrower: fails", out)
            self.assertIn("nodes: A", out)
            code, out, err = run_cli("properties", str(graph), str(table), "concepts", "--json")
            self.assertEqual(code, 0, err)
            body = json.loads(out)
            self.assertEqual(body["tool"]["version"], topontology.__version__)
            self.assertFalse(body["properties"][0]["holds"])
            code, canonical, err = run_cli("properties", str(graph), str(table), "concepts", "--format", "json")
            self.assertEqual(canonical, out)
            code, out, err = run_cli(
                "properties", str(graph), str(table), "concepts",
                "--format", "mermaid", "--class", "hierarchy", "--property", "acyclic")
            self.assertEqual(code, 0, err)
            self.assertIn("flowchart TD", out)
            self.assertIn("narrower", out)
            code, out, err = run_cli(
                "properties", str(graph), str(table), "concepts",
                "--format", "mermaid", "--class", "hierarchy", "--property", "transitive")
            self.assertEqual(code, 1)
            self.assertIn("unknown property", err)
            self.assertNotIn("Traceback", err)
            dag = _write(Path(tmp), "dag.json", {"edges": [_edge("A", "B", "narrower")]})
            code, out, err = run_cli(
                "properties", str(dag), str(table), "concepts",
                "--format", "mermaid", "--relation", "narrower", "--property", "transitive")
            self.assertEqual(code, 1)
            self.assertEqual(err, "property holds; no witness\n")
            code, out, err = run_cli(
                "properties", str(graph), str(table), "concepts", "--format", "mermaid")
            self.assertEqual(code, 1)
            self.assertIn("--property", err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "properties", str(graph), str(table), "concepts",
                "--format", "mermaid", "--property", "acyclic")
            self.assertEqual(code, 1)
            self.assertIn("--class", err)
            code, out, err = run_cli(
                "properties", str(graph), str(table), "concepts",
                "--format", "mermaid", "--class", "hierarchy", "--relation", "narrower", "--property", "acyclic")
            self.assertEqual(code, 1)
            self.assertIn("--class", err)
            plain = _write(Path(tmp), "plain.json", {"demo": {"narrower": "hierarchy"}})
            code, out, err = run_cli("properties", str(graph), str(plain), "demo", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("properties: (none)", out)
            self.assertNotIn("regressed:", out)

    def test_baseline_graph_report_and_fail_on_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            table = _write(root, "table.json", _table(
                classes={"hierarchy": {"acyclic": {}}, "similarity": {"symmetric": {}}},
            ))
            before = _write(root, "before.json", {"edges": [
                _edge("A", "B", "narrower"),
                _edge("A", "B", "similarTo"),
            ]})
            after = _write(root, "after.json", {"edges": [
                _edge("A", "B", "narrower"),
                _edge("B", "A", "narrower"),
                _edge("A", "B", "similarTo"),
                _edge("B", "A", "similarTo"),
            ]})
            code, out, err = run_cli(
                "properties", str(after), str(table), "concepts",
                "--baseline", str(before), "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("baseline:", out)
            self.assertIn("regressed: class hierarchy acyclic", out)
            self.assertIn("recovered: class similarity symmetric", out)
            code, out, err = run_cli(
                "properties", str(before), str(table), "concepts",
                "--baseline", str(before), "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("regressed: (none)", out)
            self.assertIn("recovered: (none)", out)
            code, out, err = run_cli(
                "properties", str(after), str(table), "concepts",
                "--baseline", str(before), "--fail-on-regression", "--json")
            self.assertEqual(code, 1, err)
            self.assertEqual(err, "")
            self.assertEqual(json.loads(out)["regression"]["regressed"][0]["property"], "acyclic")
            report = _write(root, "report.json", json.loads(out))
            quiet = _write(root, "quiet.json", {"edges": [
                _edge("A", "B", "narrower"),
                _edge("A", "B", "similarTo"),
                _edge("B", "A", "similarTo"),
            ]})
            code, out, err = run_cli(
                "properties", str(quiet), str(table), "concepts",
                "--baseline-report", str(report), "--fail-on-regression")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["regression"]["regressed"], [])
            code, out, err = run_cli(
                "properties", str(after), str(table), "concepts", "--fail-on-regression")
            self.assertEqual(code, 1)
            self.assertIn("--fail-on-regression", err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "properties", str(after), str(table), "concepts",
                "--baseline", str(before), "--baseline-report", str(report))
            self.assertEqual(code, 1)
            self.assertIn("only one", err)
            destination = root / "out.json"
            code, out, err = run_cli(
                "properties", str(quiet), str(table), "concepts", "-o", str(destination))
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertIn("properties", json.loads(destination.read_text(encoding="utf-8")))
            partners = _write(root, "partners.json", _table(relations={
                "narrower": {"disjoint_with": [{"relation": "similarTo"}, {"relation": "blocks"}]},
            }))
            code, out, err = run_cli(
                "properties", str(after), str(partners), "concepts",
                "--format", "mermaid", "--relation", "narrower", "--property", "disjoint_with")
            self.assertEqual(code, 1)
            self.assertIn("--partner", err)
            self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
