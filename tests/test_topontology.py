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


def run_cli(*args):
    completed = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), *args],
        cwd=ROOT,
        env=os.environ.copy(),
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
        self.assertIn("%% topontology 0.1.0", out)
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
        completed = subprocess.run(
            [sys.executable, "-S", str(ROOT / "topology.py")],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("topontology.py", completed.stderr)
