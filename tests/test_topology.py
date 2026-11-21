import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "ONTO", ROOT.parent / "ONTO"):
    if (candidate / "knowledgegraph.py").exists():
        sys.path.insert(0, str(candidate))
        break

sys.path.insert(0, str(ROOT))
import topology

def run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = topology.main(argv)
    return code, buf.getvalue()

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


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.report = topology.summarize(GRAPH, CONCEPTS)

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
        self.assertIsNotNone(similarity["order_cycle"])

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

    def test_empty_class_and_relation_field(self):
        report = topology.summarize(
            {"edges": [{"source": "A", "relation": "narrower", "target": "B"}]},
            {"narrower": "hierarchy", "similarTo": "similarity"},
        )
        self.assertEqual(report["classes"]["similarity"]["edges"], 0)
        self.assertEqual(report["classes"]["similarity"]["depth"], 0)
        self.assertEqual(report["classes"]["hierarchy"]["edges"], 1)
        self.assertEqual(report["classes"]["hierarchy"]["depth"], 1)


class CliTests(unittest.TestCase):
    def test_json_default_and_text_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp) / "graph.json"
            table = Path(tmp) / "table.json"
            graph.write_text(json.dumps(GRAPH), encoding="utf-8")
            table.write_text(json.dumps({"concepts": CONCEPTS}), encoding="utf-8")
            self.assertEqual(run_main([str(graph), str(table), "concepts"])[0], 0)
            self.assertEqual(run_main([str(graph), str(table), "concepts", "--format", "text"])[0], 0)
            self.assertEqual(run_main([str(graph), str(table), "concepts", "--json"])[0], 0)
            self.assertEqual(run_main([str(graph), str(table), "missing"])[0], 1)


if __name__ == "__main__":
    unittest.main()
