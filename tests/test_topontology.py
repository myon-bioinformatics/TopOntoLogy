"""Hand-computed graphs for class metrics, comparison, and deterministic CLI output."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import onto_path
import topontology
import topology

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "topontology.py"

CONCEPTS = {
    "narrower": "hierarchy",
    "similarTo": "similarity",
}

GRAPH = {
    "nodes": [{"id": "A", "name": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
    "edges": [
        {"source": "A", "kind": "narrower", "target": "B"},
        {"source": "B", "kind": "narrower", "target": "C"},
        {"source": "A", "kind": "narrower", "target": "C"},
        {"source": "B", "kind": "similarTo", "target": "C"},
        {"source": "C", "kind": "similarTo", "target": "B"},
        {"source": "D", "relation": "hasName", "target": "A"},
    ],
}


def run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = topontology.main(argv)
    return code, buf.getvalue()


def _traced_command(script, args, env):
    """Run a script under python -S, and under coverage when a parent measurement is active.

    ``python -S`` does not import site, so the coverage package is added to
    PYTHONPATH for the child instead of being imported as a site customization.
    """
    if not env.get("COVERAGE_PROCESS_START"):
        return [sys.executable, "-S", str(script), *args]
    import coverage
    package_root = str(Path(coverage.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    bootstrap = (
        "import runpy, sys\n"
        "import coverage\n"
        "coverage.process_startup()\n"
        "sys.argv = sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    return [sys.executable, "-S", "-c", bootstrap, str(script), *args]


def run_cli(*args, env=None):
    merged = os.environ.copy()
    if env:
        for key, value in env.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
    completed = subprocess.run(
        _traced_command(SCRIPT, args, merged),
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.report = topontology.summarize(GRAPH, CONCEPTS)

    def test_unclassified_and_existing_fields(self):
        self.assertEqual(self.report["unclassified_edges"], 1)
        hierarchy = self.report["classes"]["hierarchy"]
        self.assertEqual(hierarchy["edges"], 3)
        self.assertEqual(hierarchy["incident_nodes"], 3)
        self.assertEqual(hierarchy["multiple_sources"], ["C"])
        self.assertEqual(hierarchy["sample_cycles"], [])

    def test_similarity_uses_onto_cycles(self):
        similarity = self.report["classes"]["similarity"]
        self.assertEqual(similarity["edges"], 2)
        self.assertEqual(similarity["sample_cycles"], [["B", "C", "B"]])
        self.assertIsNone(similarity["depth"])
        self.assertEqual(similarity["order_cycle"], ["B", "C", "B"])

    def test_degree_components_and_depth(self):
        hierarchy = self.report["classes"]["hierarchy"]
        self.assertEqual(hierarchy["degree"], {
            "max_in": 2, "max_out": 2, "max_total": 2, "sources": 1, "sinks": 1,
        })
        self.assertEqual(hierarchy["weakly_connected_components"], {"count": 1, "sizes": [3]})
        self.assertEqual(hierarchy["depth"], 2)
        self.assertIsNone(hierarchy["order_cycle"])
        similarity = self.report["classes"]["similarity"]
        self.assertEqual(similarity["weakly_connected_components"], {"count": 1, "sizes": [2]})
        self.assertEqual(similarity["degree"]["sources"], 0)
        self.assertEqual(similarity["degree"]["sinks"], 0)

    def test_independent_cycles_match_the_hand_count(self):
        # hierarchy is A→B, B→C, A→C: one undirected triangle, no directed cycle.
        # E=3, V=3, C=1, so E-V+C=1 while depth stays 2.
        hierarchy = self.report["classes"]["hierarchy"]
        self.assertEqual(hierarchy["edges"], 3)
        self.assertEqual(hierarchy["incident_nodes"], 3)
        self.assertEqual(hierarchy["weakly_connected_components"]["count"], 1)
        self.assertEqual(hierarchy["independent_cycles"], 1)
        self.assertEqual(
            hierarchy["independent_cycles"],
            hierarchy["edges"] - hierarchy["incident_nodes"] + hierarchy["weakly_connected_components"]["count"],
        )
        # similarity is the mutual link B↔C. E=2, V=2, C=1, E-V+C=1.
        similarity = self.report["classes"]["similarity"]
        self.assertEqual(similarity["edges"], 2)
        self.assertEqual(similarity["incident_nodes"], 2)
        self.assertEqual(similarity["weakly_connected_components"]["count"], 1)
        self.assertEqual(similarity["independent_cycles"], 1)

    def test_split_graph_catches_a_component_miscount(self):
        """Two separate edges. E=2, V=4, C=2, so E-V+C=0.

        Reporting one component, or one component per node, fails the literal
        count. Those mistakes also move independent_cycles off 0.
        """
        report = topontology.summarize(
            {"edges": [
                {"source": "A", "kind": "narrower", "target": "B"},
                {"source": "C", "kind": "narrower", "target": "D"},
            ]},
            {"narrower": "hierarchy"},
        )
        row = report["classes"]["hierarchy"]
        self.assertEqual(row["edges"], 2)
        self.assertEqual(row["incident_nodes"], 4)
        self.assertEqual(row["weakly_connected_components"], {"count": 2, "sizes": [2, 2]})
        self.assertEqual(row["independent_cycles"], 0)
        self.assertEqual(row["depth"], 1)
        self.assertEqual(
            row["independent_cycles"],
            row["edges"] - row["incident_nodes"] + row["weakly_connected_components"]["count"],
        )

    def test_self_loop_is_one_independent_cycle(self):
        row = topontology.summarize(
            {"edges": [{"source": "A", "kind": "narrower", "target": "A"}]},
            {"narrower": "hierarchy"},
        )["classes"]["hierarchy"]
        self.assertEqual(row["edges"], 1)
        self.assertEqual(row["incident_nodes"], 1)
        self.assertEqual(row["weakly_connected_components"]["count"], 1)
        self.assertEqual(row["independent_cycles"], 1)
        self.assertIsNone(row["depth"])
        self.assertEqual(row["order_cycle"], ["A", "A"])

    def test_empty_class_and_relation_field(self):
        report = topontology.summarize(
            {"edges": [{"source": "A", "relation": "narrower", "target": "B"}]},
            {"narrower": "hierarchy", "similarTo": "similarity"},
        )
        self.assertEqual(report["classes"]["similarity"]["edges"], 0)
        self.assertEqual(report["classes"]["similarity"]["depth"], 0)
        self.assertEqual(report["classes"]["similarity"]["independent_cycles"], 0)
        self.assertEqual(report["classes"]["hierarchy"]["edges"], 1)
        self.assertEqual(report["classes"]["hierarchy"]["depth"], 1)
        self.assertEqual(report["classes"]["hierarchy"]["independent_cycles"], 0)

    def test_summarize_import_remains_on_topology(self):
        self.assertIs(topology.summarize, topontology.summarize)


class CliTests(unittest.TestCase):
    def test_json_default_and_text_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            table = Path(tmp) / "table.json"
            graph.write_text(json.dumps(GRAPH), encoding="utf-8")
            table.write_text(json.dumps({"concepts": CONCEPTS}), encoding="utf-8")
            code, out = run_main([str(graph), str(table), "concepts"])
            self.assertEqual(code, 0)
            body = json.loads(out)
            self.assertEqual(body["tool"]["version"], topontology.__version__)
            self.assertEqual(body["classes"]["hierarchy"]["independent_cycles"], 1)
            self.assertEqual(body["provenance"]["graph"]["sha256"], hashlib.sha256(graph.read_bytes()).hexdigest())
            self.assertEqual(
                body["provenance"]["classification"]["sha256"],
                hashlib.sha256(table.read_bytes()).hexdigest(),
            )
            code, text = run_main([str(graph), str(table), "concepts", "--format", "text"])
            self.assertEqual(code, 0)
            self.assertIn(f"tool: topontology {topontology.__version__}", text)
            self.assertIn("independent_cycles: 1", text)
            self.assertIn(body["provenance"]["graph"]["sha256"], text)
            self.assertEqual(run_main([str(graph), str(table), "concepts", "--json"])[0], 0)
            self.assertEqual(run_main([str(graph), str(table), "missing"])[0], 1)

    def test_canonical_json_matches_committed_output(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--format", "canonical")
        self.assertEqual(code, 0, err)
        same, other, other_err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--format", "json")
        self.assertEqual(same, 0, other_err)
        flagged, flagged_out, flagged_err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--json")
        self.assertEqual(flagged, 0, flagged_err)
        expected = (ROOT / "tests/expected/summary.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        self.assertEqual(other, expected)
        self.assertEqual(flagged_out, expected)
        _, again, _ = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--format", "canonical")
        self.assertEqual(again, out)

    def test_mermaid_matches_committed_output(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--format", "mermaid", "--class", "hierarchy")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/hierarchy.mmd").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        self.assertIn(f"%% topontology {topontology.__version__}", out)
        self.assertIn("sha256:", out)
        self.assertIn('n0 -->|"narrower"| n1', out)
        self.assertEqual(run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--format", "mermaid")[0], 1)

    def test_string_nodes_have_a_deterministic_diagram(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            table = Path(tmp) / "table.json"
            graph.write_text(json.dumps({
                "nodes": ["A", "B"],
                "edges": [{"source": "B", "kind": "narrower", "target": "A"}],
            }), encoding="utf-8")
            table.write_text(json.dumps({"demo": {"narrower": "hierarchy"}}), encoding="utf-8")
            code, first = run_main([str(graph), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
            code2, second = run_main([str(graph), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
        self.assertEqual(code, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(first, second)
        self.assertIn('["A"]', first)
        self.assertIn('["B"]', first)

    def test_two_graph_comparison_names_both_inputs(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--compare", "tests/fixtures/graph-extended.json", "--format", "canonical")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/compare.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        left = body["provenance"]["left"]
        right = body["provenance"]["right"]
        self.assertEqual(left["graph"]["path"], "tests/fixtures/graph.json")
        self.assertEqual(right["graph"]["path"], "tests/fixtures/graph-extended.json")
        self.assertEqual(left["graph"]["sha256"], hashlib.sha256(
            (ROOT / "tests/fixtures/graph.json").read_bytes()).hexdigest())
        self.assertEqual(right["graph"]["sha256"], hashlib.sha256(
            (ROOT / "tests/fixtures/graph-extended.json").read_bytes()).hexdigest())
        self.assertNotEqual(left["graph"]["sha256"], right["graph"]["sha256"])
        # The extra C→E edge lengthens the hierarchy by one edge and one node.
        # E=4, V=4, C=1, so the independent-cycle count stays 1.
        delta = body["classes"]["hierarchy"]["delta"]
        self.assertEqual(delta["edges"], 1)
        self.assertEqual(delta["incident_nodes"], 1)
        self.assertEqual(delta["depth"], 1)
        self.assertEqual(delta["independent_cycles"], 0)
        self.assertEqual(delta["weakly_connected_components"], 0)
        self.assertEqual(body["classes"]["hierarchy"]["right"]["depth"], 3)
        self.assertEqual(body["classes"]["hierarchy"]["right"]["independent_cycles"], 1)
        self.assertIsNone(body["classes"]["similarity"]["delta"]["depth"])
        text_code, text, text_err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--compare", "tests/fixtures/graph-extended.json", "--format", "text")
        self.assertEqual(text_code, 0, text_err)
        self.assertIn("left_graph_sha256:", text)
        self.assertIn("right_graph_sha256:", text)
        self.assertIn(left["graph"]["sha256"], text)
        self.assertIn(right["graph"]["sha256"], text)

    def test_two_class_comparison_names_both_classes(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--class", "hierarchy", "--compare-class", "similarity", "--format", "json")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["provenance"]["left"]["class"], "hierarchy")
        self.assertEqual(body["provenance"]["right"]["class"], "similarity")
        self.assertEqual(body["provenance"]["left"]["graph"]["sha256"], body["provenance"]["right"]["graph"]["sha256"])
        self.assertEqual(body["comparison"]["delta"]["edges"], -1)
        self.assertIsNone(body["comparison"]["delta"]["depth"])
        self.assertEqual(body["comparison"]["left"]["metrics"]["independent_cycles"], 1)
        self.assertEqual(body["comparison"]["right"]["metrics"]["independent_cycles"], 1)

    def test_output_path_is_the_only_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.json"
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--format", "canonical", "-o", str(dest))
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertEqual(dest.read_text(encoding="utf-8"),
                             (ROOT / "tests/expected/summary.json").read_text(encoding="utf-8"))

    def test_rejected_flag_combinations(self):
        self.assertEqual(run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--compare", "tests/fixtures/graph-extended.json", "--compare-class", "similarity",
            "--class", "hierarchy")[0], 1)
        self.assertEqual(run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--format", "text", "--json")[0], 1)
        self.assertEqual(run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", "--cycle-limit", "-1")[0], 1)

    def test_sources_do_not_search_checkouts(self):
        for name in ("topontology.py", "topology.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("sys.path", text)

    def test_topology_script_is_not_a_second_command(self):
        traced_env = os.environ.copy()
        completed = subprocess.run(
            _traced_command(ROOT / "topology.py", [], traced_env),
            cwd=ROOT,
            env=traced_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("topontology.py", completed.stderr)


NUMERIC_GRAPH = {
    "nodes": [{"id": 1, "name": "A"}, {"id": 2}, {"id": 3}, {"id": 4}],
    "edges": [
        {"source": 1, "kind": "narrower", "target": 2},
        {"source": 2, "kind": "narrower", "target": 3},
        {"source": 1, "kind": "narrower", "target": 3},
        {"source": 2, "kind": "similarTo", "target": 3},
        {"source": 3, "kind": "similarTo", "target": 2},
        {"source": 4, "relation": "hasName", "target": 1},
    ],
}

NUMERIC_EXTENDED = {
    "nodes": [{"id": 1, "name": "A"}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5, "name": "E"}],
    "edges": [
        {"source": 1, "kind": "narrower", "target": 2},
        {"source": 2, "kind": "narrower", "target": 3},
        {"source": 1, "kind": "narrower", "target": 3},
        {"source": 3, "kind": "narrower", "target": 5},
        {"source": 2, "kind": "similarTo", "target": 3},
        {"source": 3, "kind": "similarTo", "target": 2},
        {"source": 4, "relation": "hasName", "target": 1},
    ],
}

MIXED_GRAPH = {
    "nodes": [{"id": 1, "name": "int-one"}, {"id": "1", "name": "str-one"}, {"id": "A"}],
    "edges": [
        {"source": 1, "kind": "narrower", "target": "1"},
        {"source": "1", "kind": "narrower", "target": 1},
        {"source": "A", "kind": "narrower", "target": "B"},
        {"source": 2, "kind": "similarTo", "target": "2"},
    ],
}


def _write_pair(tmp, graph, classes=None, profile="demo"):
    root = Path(tmp)
    graph_path = root / "graph.json"
    table_path = root / "table.json"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    table_path.write_text(json.dumps({profile: classes or CONCEPTS}), encoding="utf-8")
    return graph_path, table_path


class NodeIdTests(unittest.TestCase):
    def test_numeric_ids_match_the_string_graph_metrics(self):
        report = topontology.summarize(NUMERIC_GRAPH, CONCEPTS)
        hierarchy = report["classes"]["hierarchy"]
        self.assertEqual(hierarchy["edges"], 3)
        self.assertEqual(hierarchy["incident_nodes"], 3)
        self.assertEqual(hierarchy["multiple_sources"], [3])
        self.assertEqual(hierarchy["weakly_connected_components"], {"count": 1, "sizes": [3]})
        self.assertEqual(hierarchy["independent_cycles"], 1)
        self.assertEqual(hierarchy["depth"], 2)
        self.assertIsNone(hierarchy["order_cycle"])
        similarity = report["classes"]["similarity"]
        self.assertEqual(similarity["sample_cycles"], [[2, 3, 2]])
        self.assertEqual(similarity["order_cycle"], [2, 3, 2])
        self.assertIsNone(similarity["depth"])
        self.assertEqual(similarity["independent_cycles"], 1)

    def test_mixed_ids_stay_distinct_in_summarize(self):
        report = topontology.summarize(MIXED_GRAPH, CONCEPTS)
        hierarchy = report["classes"]["hierarchy"]
        self.assertEqual(hierarchy["edges"], 3)
        self.assertEqual(hierarchy["incident_nodes"], 4)
        self.assertEqual(hierarchy["weakly_connected_components"]["count"], 2)
        self.assertEqual(hierarchy["weakly_connected_components"]["sizes"], [2, 2])
        self.assertEqual(hierarchy["independent_cycles"], 1)
        self.assertIsNone(hierarchy["depth"])
        cycle = hierarchy["order_cycle"]
        self.assertEqual(set(map(topontology.node_sort_key, cycle[:-1])), {
            topontology.node_sort_key(1), topontology.node_sort_key("1"),
        })
        self.assertEqual(cycle[0], cycle[-1])
        self.assertIsInstance(cycle[0], type(cycle[-1]))
        # 1 and "1" remain distinct in the reported cycle.
        self.assertTrue(any(node == 1 and not isinstance(node, bool) and not isinstance(node, str) for node in cycle))
        self.assertTrue(any(node == "1" for node in cycle))
        similarity = report["classes"]["similarity"]
        self.assertEqual(similarity["edges"], 1)
        self.assertEqual(similarity["incident_nodes"], 2)
        self.assertEqual(similarity["weakly_connected_components"], {"count": 1, "sizes": [2]})
        self.assertEqual(similarity["depth"], 1)

    def test_numeric_and_mixed_cli_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            numeric, table = _write_pair(tmp, NUMERIC_GRAPH)
            extended = Path(tmp) / "extended.json"
            extended.write_text(json.dumps(NUMERIC_EXTENDED), encoding="utf-8")
            mixed = Path(tmp) / "mixed.json"
            mixed.write_text(json.dumps(MIXED_GRAPH), encoding="utf-8")

            code, out = run_main([str(numeric), str(table), "demo"])
            self.assertEqual(code, 0)
            body = json.loads(out)
            self.assertEqual(body["classes"]["hierarchy"]["multiple_sources"], [3])
            self.assertEqual(body["classes"]["similarity"]["sample_cycles"], [[2, 3, 2]])

            code, text = run_main([str(numeric), str(table), "demo", "--format", "text"])
            self.assertEqual(code, 0)
            self.assertIn("multiple_sources: 3", text)
            self.assertIn("2 → 3 → 2", text)

            code, mermaid = run_main(
                [str(numeric), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
            self.assertEqual(code, 0)
            self.assertIn('["A"]', mermaid)
            self.assertIn('["2"]', mermaid)
            self.assertIn('["3"]', mermaid)
            self.assertIn('-->|"narrower"|', mermaid)

            code, compared = run_main([
                str(numeric), str(table), "demo", "--compare", str(extended), "--format", "json",
            ])
            self.assertEqual(code, 0)
            delta = json.loads(compared)["classes"]["hierarchy"]["delta"]
            self.assertEqual(delta["edges"], 1)
            self.assertEqual(delta["incident_nodes"], 1)
            self.assertEqual(delta["depth"], 1)

            code, mixed_json = run_main([str(mixed), str(table), "demo"])
            self.assertEqual(code, 0)
            mixed_body = json.loads(mixed_json)
            cycle = mixed_body["classes"]["hierarchy"]["order_cycle"]
            self.assertTrue(any(node == 1 and not isinstance(node, str) for node in cycle))
            self.assertTrue(any(node == "1" for node in cycle))

            code, mixed_text = run_main([str(mixed), str(table), "demo", "--format", "text"])
            self.assertEqual(code, 0)
            self.assertIn("1 → 1 → 1", mixed_text)
            self.assertIn("independent_cycles: 1", mixed_text)

            code, mixed_mermaid = run_main(
                [str(mixed), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
            self.assertEqual(code, 0, mixed_mermaid)
            # Display names stay distinct; ONTO remaps IDs to n0, n1, ...
            self.assertIn('["int-one"]', mixed_mermaid)
            self.assertIn('["str-one"]', mixed_mermaid)
            self.assertIn('["A"]', mixed_mermaid)
            self.assertIn('["B"]', mixed_mermaid)
            self.assertEqual(mixed_mermaid.count("narrower"), 3)

            code, mixed_compare = run_main([
                str(mixed), str(table), "demo", "--class", "hierarchy",
                "--compare-class", "similarity", "--format", "json",
            ])
            self.assertEqual(code, 0)
            comparison = json.loads(mixed_compare)["comparison"]
            self.assertEqual(comparison["left"]["metrics"]["incident_nodes"], 4)
            self.assertEqual(comparison["right"]["metrics"]["incident_nodes"], 2)

    def test_canonical_cycle_helpers(self):
        self.assertEqual(topontology._canonical_cycle([]), [])
        self.assertEqual(topontology._canonical_cycle(["C", "A", "B"]), ["A", "B", "C"])
        self.assertEqual(topontology._untag_path([]), [])

    def test_mermaid_disambiguates_colliding_display_labels(self):
        colliding = {
            "nodes": [{"id": 1}, {"id": "1"}],
            "edges": [{"source": 1, "kind": "narrower", "target": "1"}],
        }
        unique = {
            "nodes": [{"id": 1, "name": "A"}, {"id": 2}, {"id": "1", "name": "str-one"}],
            "edges": [
                {"source": 1, "kind": "narrower", "target": 2},
                {"source": "1", "kind": "narrower", "target": 2},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "table.json"
            table.write_text(json.dumps({"demo": CONCEPTS}), encoding="utf-8")
            collision = Path(tmp) / "collision.json"
            collision.write_text(json.dumps(colliding), encoding="utf-8")
            distinct = Path(tmp) / "unique.json"
            distinct.write_text(json.dumps(unique), encoding="utf-8")

            code, mermaid = run_main(
                [str(collision), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
            self.assertEqual(code, 0, mermaid)
            boxes = [line.strip() for line in mermaid.splitlines() if '["' in line and line.strip().startswith("n")]
            labels = []
            for line in boxes:
                start = line.index('["') + 2
                end = line.index('"]', start)
                labels.append(line[start:end])
            self.assertEqual(len(labels), 2)
            self.assertNotEqual(labels[0], labels[1])
            self.assertIn("1", labels)
            self.assertIn("int:1", labels)

            code, unchanged = run_main(
                [str(distinct), str(table), "demo", "--format", "mermaid", "--class", "hierarchy"])
            self.assertEqual(code, 0, unchanged)
            self.assertIn('["A"]', unchanged)
            self.assertIn('["2"]', unchanged)
            self.assertIn('["str-one"]', unchanged)
            self.assertNotIn("int:1", unchanged)

    def test_node_labels_skip_unknown_shapes(self):
        labels = topontology._node_labels({
            "nodes": ["A", {"name": "no-id"}, 99, None, {"id": "B", "name": "Bee"}],
        })
        self.assertEqual(labels, {"A": "A", "B": "Bee"})
        self.assertEqual(topontology._node_labels({}), {})


class CliErrorAndCoverageTests(unittest.TestCase):
    def test_missing_kind_and_relation_is_a_clean_value_error(self):
        with self.assertRaises(ValueError) as raised:
            topontology.edge_relation({"source": "A", "target": "B"})
        self.assertIn("kind", str(raised.exception))
        self.assertIn("relation", str(raised.exception))
        with tempfile.TemporaryDirectory() as tmp:
            graph, table = _write_pair(tmp, {"edges": [{"source": "A", "target": "B"}]})
            code, out, err = run_cli(str(graph), str(table), "demo")
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("missing both 'kind' and 'relation'", err)
            self.assertNotIn("Traceback", err)

    def test_missing_endpoint_is_a_clean_value_error(self):
        with self.assertRaises(ValueError) as raised:
            topontology.summarize(
                {"edges": [{"kind": "narrower", "target": "B"}]},
                {"narrower": "hierarchy"},
            )
        self.assertIn("source", str(raised.exception))
        with tempfile.TemporaryDirectory() as tmp:
            graph, table = _write_pair(tmp, {"edges": [{"kind": "narrower", "target": "B"}]})
            code, out, err = run_cli(str(graph), str(table), "demo")
            self.assertEqual(code, 1)
            self.assertIn("source", err)
            self.assertNotIn("Traceback", err)

    def test_short_form_ignores_dropped_env_vars(self):
        expected = (ROOT / "tests/expected/summary.json").read_text(encoding="utf-8")
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "demo",
            "--classification", "tests/fixtures/classes.json",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, expected)
        # Dropped variables are not a second way to pass the graph, table, or profile.
        code, out, err = run_cli(env={
            "TOPONTOLOGY_GRAPH": "tests/fixtures/graph.json",
            "TOPONTOLOGY_CLASSIFICATION": "tests/fixtures/classes.json",
            "TOPONTOLOGY_PROFILE": "demo",
        })
        self.assertEqual(code, 1)
        self.assertIn("pass the graph path", err)
        self.assertNotIn("TOPONTOLOGY_GRAPH", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli("tests/fixtures/graph.json")
        self.assertEqual(code, 1)
        self.assertIn("pass the profile", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            env={
                "TOPONTOLOGY_GRAPH": "/missing/graph.json",
                "TOPONTOLOGY_CLASSIFICATION": "/missing/table.json",
                "TOPONTOLOGY_PROFILE": "missing",
            },
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, expected)

    def test_error_branches_and_remaining_cli_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, table = _write_pair(tmp, GRAPH)
            missing = str(Path(tmp) / "nope.json")
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{", encoding="utf-8")
            not_graph = Path(tmp) / "not-graph.json"
            not_graph.write_text("[]", encoding="utf-8")
            empty_profile = Path(tmp) / "empty-profile.json"
            empty_profile.write_text(json.dumps({"demo": {}}), encoding="utf-8")
            no_nodes = Path(tmp) / "no-nodes.json"
            no_nodes.write_text(json.dumps({"edges": [{"source": "A", "kind": "narrower", "target": "B"}]}),
                                encoding="utf-8")
            dest = Path(tmp) / "class.txt"

            code, out, err = run_cli(missing, str(table), "demo")
            self.assertEqual(code, 1)
            self.assertTrue(err)
            self.assertNotIn("Traceback", err)

            code, out, err = run_cli(str(bad_json), str(table), "demo")
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)

            code, out, err = run_cli(str(not_graph), str(table), "demo")
            self.assertEqual(code, 1)
            self.assertIn("edges array", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--format", "mermaid", "--compare", "tests/fixtures/graph-extended.json",
                "--class", "hierarchy")
            self.assertEqual(code, 1)
            self.assertIn("mermaid", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--format", "mermaid", "--class", "hierarchy", "--compare-class", "similarity")
            self.assertEqual(code, 1)
            self.assertIn("mermaid", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--compare-class", "similarity")
            self.assertEqual(code, 1)
            self.assertIn("--compare-class requires --class", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--class", "missing")
            self.assertEqual(code, 1)
            self.assertIn("unknown class for --class", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--class", "hierarchy", "--compare-class", "missing")
            self.assertEqual(code, 1)
            self.assertIn("unknown class for --compare-class", err)

            code, out, err = run_cli(str(graph), str(empty_profile), "demo", "--class", "hierarchy")
            self.assertEqual(code, 1)
            self.assertIn("(none)", err)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--class", "hierarchy", "--format", "text", "-o", str(dest))
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertIn("hierarchy:", dest.read_text(encoding="utf-8"))
            self.assertIn("unclassified_edges: 1", dest.read_text(encoding="utf-8"))

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--class", "hierarchy", "--compare-class", "similarity", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("left_class: hierarchy", out)
            self.assertIn("right_class: similarity", out)
            self.assertIn("hierarchy vs similarity:", out)

            code, out, err = run_cli(
                str(no_nodes), str(table), "demo", "--format", "mermaid", "--class", "hierarchy")
            self.assertEqual(code, 0, err)
            self.assertIn("flowchart TD", out)

            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--cycle-limit", "0")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["classes"]["similarity"]["sample_cycles"], [])

            # table that is not an object
            not_table = Path(tmp) / "not-table.json"
            not_table.write_text("[]", encoding="utf-8")
            code, out, err = run_cli(str(graph), str(not_table), "demo")
            self.assertEqual(code, 1)
            self.assertIn("unknown profile", err)

            bad_profile = Path(tmp) / "bad-profile.json"
            bad_profile.write_text(json.dumps({"demo": []}), encoding="utf-8")
            code, out, err = run_cli(str(graph), str(bad_profile), "demo")
            self.assertEqual(code, 1)
            self.assertIn("unknown profile", err)

            # empty weakly-connected sizes render as (none)
            empty_text = topontology._class_lines("empty", {
                "edges": 0,
                "incident_nodes": 0,
                "multiple_sources": [],
                "sample_cycles": [],
                "degree": {"max_in": 0, "max_out": 0, "max_total": 0, "sources": 0, "sinks": 0},
                "weakly_connected_components": {"count": 0, "sizes": []},
                "independent_cycles": 0,
                "depth": None,
                "order_cycle": None,
                "cycle_basis": {"cycles": [], "independent_cycles": 0},
            })
            self.assertIn("multiple_sources: (none)", "\n".join(empty_text))
            self.assertIn("cycle_basis: 0 (none)", "\n".join(empty_text))
            self.assertIn("sample_cycles: (none)", "\n".join(empty_text))
            self.assertIn("weakly_connected_components: 0 ((none))", "\n".join(empty_text))
            self.assertIn("depth: (undefined, cycle)", "\n".join(empty_text))

            # unhashable endpoint surfaces as TypeError to the CLI without a traceback
            unhashable = Path(tmp) / "unhashable.json"
            unhashable.write_text(json.dumps({
                "edges": [{"source": {"x": 1}, "kind": "narrower", "target": "B"}],
            }), encoding="utf-8")
            code, out, err = run_cli(str(unhashable), str(table), "demo")
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)

    def test_json_flag_with_canonical_format(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--json", "--format", "canonical")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/summary.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
