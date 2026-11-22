"""Hand-computed sigma bands and class-boundary deltas."""
import json
import statistics
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_topontology import ROOT, run_cli

STAR = {"to": "star"}


def _star(leaves, center="C"):
    return {"edges": [{"source": f"L{i}", "kind": "to", "target": center} for i in range(leaves)]}


def _total(report, scope="classes", name="star"):
    return report[scope][name]["nodes"]["total_degree"]


class SigmaBandTests(unittest.TestCase):
    def test_center_of_ten_leaves_is_beyond_three_sigma(self):
        # Ten leaves of total degree 1 and one center of total degree 10.
        # z(center) = sqrt(10) > 3, and every leaf stays inside 1σ.
        report = topontology.anomaly_report(_star(10), STAR)
        metric = _total(report)
        self.assertEqual(metric["status"], "ok")
        self.assertEqual(metric["count"], 11)
        values = [1] * 10 + [10]
        mean = statistics.fmean(values)
        sigma = statistics.pstdev(values)
        self.assertEqual(metric["mean"], mean)
        self.assertEqual(metric["sigma"], sigma)
        self.assertGreater((10 - mean) / sigma, 3)
        self.assertLess(abs(1 - mean) / sigma, 1)
        for level in ("1", "2", "3"):
            self.assertEqual([item["id"] for item in metric["bands"][level]], ["C"])
            self.assertEqual(metric["bands"][level][0]["value"], 10)

    def test_exact_three_sigma_is_not_beyond_that_band(self):
        # Nine leaves: the center's z is exactly 3, which is beyond 2σ and not beyond 3σ.
        metric = _total(topontology.anomaly_report(_star(9), STAR))
        self.assertEqual([item["id"] for item in metric["bands"]["1"]], ["C"])
        self.assertEqual([item["id"] for item in metric["bands"]["2"]], ["C"])
        self.assertEqual(metric["bands"]["3"], [])
        self.assertEqual(metric["bands"]["2"][0]["z"], 3)

    def test_chain_depths_beyond_one_sigma_only(self):
        # Depths 0,1,2,3,4,5,6. Mean 3, population sigma 2.
        # |z| is 1.5 for the ends and exactly 1 for depths 1 and 5, so only the ends qualify.
        edges = [{"source": "ABCDEFG"[i], "kind": "next", "target": "ABCDEFG"[i + 1]} for i in range(6)]
        metric = topontology.anomaly_report(
            {"edges": edges}, {"next": "chain"})["classes"]["chain"]["nodes"]["depth"]
        self.assertEqual(metric["mean"], 3)
        self.assertEqual(metric["sigma"], 2)
        self.assertEqual([(item["id"], item["value"]) for item in metric["bands"]["1"]], [("A", 0), ("G", 6)])
        self.assertEqual(metric["bands"]["2"], [])
        self.assertEqual(metric["bands"]["3"], [])

    def test_component_size_outlier_is_the_singleton(self):
        # Sizes 4,4,4,4, 2,2, 1. Mean 3, and only the self-loop is beyond 1σ.
        edges = [
            {"source": "A", "kind": "to", "target": "B"},
            {"source": "B", "kind": "to", "target": "C"},
            {"source": "C", "kind": "to", "target": "D"},
            {"source": "E", "kind": "to", "target": "F"},
            {"source": "G", "kind": "to", "target": "G"},
        ]
        metric = topontology.anomaly_report(
            {"edges": edges}, STAR)["classes"]["star"]["nodes"]["component_size"]
        self.assertEqual(metric["mean"], 3)
        self.assertEqual([item["id"] for item in metric["bands"]["1"]], ["G"])
        self.assertEqual(metric["bands"]["1"][0]["value"], 1)
        self.assertEqual(metric["bands"]["2"], [])

    def test_too_few_points_and_zero_variance_are_not_applicable(self):
        small = _total(topontology.anomaly_report(_star(1), STAR))
        self.assertEqual(small, {
            "status": "not applicable",
            "reason": "fewer than 3 data points",
            "count": 2,
        })
        loops = [{"source": node, "kind": "to", "target": node} for node in "ABC"]
        flat = _total(topontology.anomaly_report({"edges": loops}, STAR))
        self.assertEqual(flat["status"], "not applicable")
        self.assertEqual(flat["reason"], "zero variance")
        self.assertEqual(flat["sigma"], 0.0)
        cycled = topontology.anomaly_report(
            {"edges": [
                {"source": "A", "kind": "to", "target": "B"},
                {"source": "B", "kind": "to", "target": "C"},
                {"source": "C", "kind": "to", "target": "A"},
            ]},
            STAR,
        )["classes"]["star"]["nodes"]["depth"]
        self.assertEqual(cycled["reason"], "cycle")

    def test_shifting_one_degree_changes_the_band_report(self):
        before = topontology.anomaly_report(_star(10), STAR)
        shifted = _star(10)
        shifted["edges"] = list(shifted["edges"]) + [{"source": "L0", "kind": "to", "target": "L1"}]
        after = topontology.anomaly_report(shifted, STAR)
        self.assertNotEqual(before, after)
        self.assertNotEqual(
            _total(before)["mean"],
            _total(after)["mean"],
        )
        self.assertEqual(_total(before)["bands"]["3"][0]["value"], 10)
        leaf_values = {item["id"]: item["value"] for item in _total(after)["bands"]["1"]}
        # L0 and L1 are no longer ordinary leaves; the original center value is not the whole story.
        self.assertNotEqual(leaf_values.get("L0"), 1)

    def test_equal_scores_use_the_type_and_text_key(self):
        edges = []
        for index in range(10):
            edges.append({"source": f"a{index}", "kind": "to", "target": 1})
            edges.append({"source": f"b{index}", "kind": "to", "target": "1"})
        metric = _total(topontology.anomaly_report({"edges": edges}, STAR))
        identities = [item["id"] for item in metric["bands"]["1"]]
        self.assertEqual(identities, sorted(identities, key=topontology.node_sort_key))
        self.assertEqual(identities[:2], [1, "1"])
        self.assertIsInstance(identities[0], int)
        self.assertIsInstance(identities[1], str)

    def test_numeric_ids_stay_numeric(self):
        edges = [{"source": index, "kind": "to", "target": 0} for index in range(1, 11)]
        metric = _total(topontology.anomaly_report({"edges": edges}, STAR))
        self.assertEqual(metric["bands"]["3"][0]["id"], 0)
        self.assertIsInstance(metric["bands"]["3"][0]["id"], int)

    def test_swapping_a_class_assignment_changes_the_metrics(self):
        graph = {"edges": [
            {"source": "A", "kind": "narrower", "target": "B"},
            {"source": "B", "kind": "narrower", "target": "C"},
            {"source": "A", "kind": "broader", "target": "C"},
        ]}
        original = {"narrower": "hierarchy", "broader": "hierarchy"}
        swapped = {"narrower": "hierarchy", "broader": "similarity"}
        before = topontology.summarize(graph, original)
        after = topontology.summarize(graph, swapped)
        self.assertNotEqual(before, after)
        self.assertEqual(before["classes"]["hierarchy"]["edges"], 3)
        self.assertEqual(after["classes"]["hierarchy"]["edges"], 2)
        self.assertEqual(after["classes"]["similarity"]["edges"], 1)


class BoundaryTests(unittest.TestCase):
    def test_merge_deltas_match_the_hand_count(self):
        graph = {
            "edges": [
                {"source": "A", "kind": "narrower", "target": "B"},
                {"source": "B", "kind": "narrower", "target": "C"},
                {"source": "A", "kind": "narrower", "target": "C"},
                {"source": "B", "kind": "similarTo", "target": "C"},
                {"source": "C", "kind": "similarTo", "target": "B"},
            ]
        }
        classes = {"narrower": "hierarchy", "similarTo": "similarity"}
        merged = topontology.merged_classification(classes, ["hierarchy", "similarity"], "linked")
        compared = topontology.compare_boundaries(
            topontology.summarize(graph, classes),
            topontology.summarize(graph, merged),
        )
        # The joined class has the triangle plus the mutual link: E=5, V=3, C=1, E-V+C=3.
        linked = compared["classes"]["linked"]
        self.assertEqual(linked["left"]["edges"], 0)
        self.assertEqual(linked["right"]["edges"], 5)
        self.assertEqual(linked["right"]["incident_nodes"], 3)
        self.assertEqual(linked["right"]["independent_cycles"], 3)
        self.assertIsNone(linked["right"]["depth"])
        self.assertEqual(linked["delta"]["edges"], 5)
        self.assertEqual(linked["delta"]["independent_cycles"], 3)
        self.assertIsNone(linked["delta"]["depth"])
        self.assertEqual(linked["delta"]["directed_cycle"], 1)
        self.assertEqual(compared["classes"]["hierarchy"]["delta"]["edges"], -3)
        self.assertEqual(compared["classes"]["hierarchy"]["delta"]["depth"], -2)
        self.assertEqual(compared["classes"]["similarity"]["delta"]["edges"], -2)
        self.assertEqual(compared["classes"]["similarity"]["delta"]["sample_cycles"], -1)
        self.assertEqual(compared["classes"]["similarity"]["delta"]["directed_cycle"], -1)

    def test_split_deltas_match_the_hand_count(self):
        graph = {"edges": [
            {"source": "A", "kind": "narrower", "target": "B"},
            {"source": "B", "kind": "narrower", "target": "C"},
            {"source": "A", "kind": "broader", "target": "C"},
        ]}
        classes = {"narrower": "hierarchy", "broader": "hierarchy"}
        split = topontology.split_classification(classes, "hierarchy", {"broader": "up"})
        compared = topontology.compare_boundaries(
            topontology.summarize(graph, classes),
            topontology.summarize(graph, split),
        )
        # hierarchy keeps the chain A→B→C (depth 2, E-V+C=0). up is the single shortcut.
        self.assertEqual(compared["classes"]["hierarchy"]["right"]["edges"], 2)
        self.assertEqual(compared["classes"]["hierarchy"]["right"]["depth"], 2)
        self.assertEqual(compared["classes"]["hierarchy"]["right"]["independent_cycles"], 0)
        self.assertEqual(compared["classes"]["hierarchy"]["delta"]["edges"], -1)
        self.assertEqual(compared["classes"]["hierarchy"]["delta"]["independent_cycles"], -1)
        self.assertEqual(compared["classes"]["up"]["right"]["edges"], 1)
        self.assertEqual(compared["classes"]["up"]["right"]["depth"], 1)
        self.assertEqual(compared["classes"]["up"]["delta"]["edges"], 1)
        self.assertEqual(compared["classes"]["up"]["delta"]["incident_nodes"], 2)

    def test_second_table_matches_the_merge_mapping(self):
        graph = {"edges": [
            {"source": "A", "kind": "narrower", "target": "B"},
            {"source": "B", "kind": "similarTo", "target": "A"},
        ]}
        left = {"narrower": "hierarchy", "similarTo": "similarity"}
        right = {"narrower": "linked", "similarTo": "linked"}
        by_mapping = topontology.compare_boundaries(
            topontology.summarize(graph, left),
            topontology.summarize(graph, topontology.merged_classification(left, ["hierarchy", "similarity"], "linked")),
        )
        by_table = topontology.compare_boundaries(
            topontology.summarize(graph, left),
            topontology.summarize(graph, right),
        )
        self.assertEqual(by_mapping["classes"], by_table["classes"])

    def test_mapping_rejects_unknown_or_empty_edits(self):
        classes = {"narrower": "hierarchy", "broader": "hierarchy"}
        with self.assertRaises(ValueError):
            topontology.merged_classification(classes, ["hierarchy", "hierarchy"], "linked")
        with self.assertRaises(ValueError):
            topontology.merged_classification(classes, ["hierarchy", "missing"], "linked")
        with self.assertRaises(ValueError):
            topontology.split_classification(classes, "missing", {"narrower": "up"})
        with self.assertRaises(ValueError):
            topontology.split_classification(classes, "hierarchy", {})
        with self.assertRaises(ValueError):
            topontology.split_classification(classes, "hierarchy", {"similarTo": "up"})
        with self.assertRaises(ValueError):
            topontology.split_classification(
                {"narrower": "hierarchy", "similarTo": "similarity"},
                "hierarchy",
                {"similarTo": "up"},
            )
        with self.assertRaises(ValueError):
            topontology.split_classification(classes, "hierarchy", {"narrower": "hierarchy"})
        with self.assertRaises(ValueError):
            topontology.split_classification({"narrower": "other"}, "hierarchy", {"narrower": "up"})


class AnomalyCliTests(unittest.TestCase):
    def test_star_anomalies_match_committed_output(self):
        code, out, err = run_cli(
            "tests/fixtures/sigma-star.json", "tests/fixtures/sigma-classes.json", "demo",
            "--anomalies", "--format", "canonical")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/anomalies.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(body["tool"]["version"], topontology.__version__)
        self.assertEqual(
            [item["id"] for item in body["anomalies"]["classes"]["star"]["nodes"]["total_degree"]["bands"]["3"]],
            ["C"],
        )
        self.assertEqual(
            body["anomalies"]["classes"]["side"]["nodes"]["depth"]["reason"],
            "fewer than 3 data points",
        )
        code, text, err = run_cli(
            "tests/fixtures/sigma-star.json", "tests/fixtures/sigma-classes.json", "demo",
            "--anomalies", "--format", "text")
        self.assertEqual(code, 0, err)
        self.assertIn("beyond 3σ: C=10", text)
        self.assertIn("reason: zero variance", text)
        self.assertIn("reason: fewer than 3 data points", text)

    def test_anomalies_ignore_unclassified_relations(self):
        # tests/fixtures/graph.json has a hasName edge that is not in classes.json.
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--anomalies", "--format", "json")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["unclassified_edges"], 1)
        self.assertEqual(set(body["anomalies"]["relations"]), {"narrower", "similarTo"})
        self.assertNotIn("hasName", body["anomalies"]["relations"])
        self.assertEqual(set(body["anomalies"]["classes"]), {"hierarchy", "similarity"})
        with tempfile.TemporaryDirectory() as tmp:
            classified = Path(tmp) / "classified.json"
            graph = json.loads((ROOT / "tests/fixtures/graph.json").read_text(encoding="utf-8"))
            graph["edges"] = [
                edge for edge in graph["edges"]
                if edge.get("kind", edge.get("relation")) in {"narrower", "similarTo"}
            ]
            classified.write_text(json.dumps(graph), encoding="utf-8")
            code, only, err = run_cli(
                str(classified), "tests/fixtures/classes.json", "demo",
                "--anomalies", "--format", "json")
            self.assertEqual(code, 0, err)
            only_body = json.loads(only)
            self.assertEqual(only_body["unclassified_edges"], 0)
            self.assertEqual(body["anomalies"], only_body["anomalies"])
        code, text, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--anomalies", "--format", "text")
        self.assertEqual(code, 0, err)
        self.assertIn("unclassified_edges: 1", text)
        self.assertIn("  relations:", text)
        self.assertIn("    narrower:", text)
        self.assertIn("    similarTo:", text)
        self.assertNotIn("hasName", text)

    def test_merge_output_names_both_inputs(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--merge", "hierarchy", "--merge", "similarity", "--into", "linked",
            "--format", "canonical")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/boundary.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(body["provenance"]["left"]["classification"]["sha256"],
                         body["provenance"]["right"]["classification"]["sha256"])
        self.assertEqual(body["provenance"]["right"]["mapping"]["operation"], "merge")
        self.assertEqual(body["classes"]["linked"]["delta"]["edges"], 5)
        self.assertEqual(body["classes"]["linked"]["right"]["independent_cycles"], 3)
        code, text, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
            "--merge", "similarity", "--merge", "hierarchy", "--into", "linked",
            "--format", "text")
        self.assertEqual(code, 0, err)
        self.assertIn('"operation": "merge"', text)
        self.assertIn("linked:", text)
        self.assertIn("directed_cycle:", text)

    def test_split_and_boundary_file_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = Path(tmp) / "table.json"
            other = Path(tmp) / "other.json"
            graph = Path(tmp) / "graph.json"
            graph.write_text(json.dumps({
                "edges": [
                    {"source": "A", "kind": "narrower", "target": "B"},
                    {"source": "B", "kind": "narrower", "target": "C"},
                    {"source": "A", "kind": "broader", "target": "C"},
                ]
            }), encoding="utf-8")
            table.write_text(json.dumps({
                "demo": {"narrower": "hierarchy", "broader": "hierarchy"}
            }), encoding="utf-8")
            other.write_text(json.dumps({
                "demo": {"narrower": "hierarchy", "broader": "up"}
            }), encoding="utf-8")
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "hierarchy",
                "--assign", "broader=up", "--format", "json")
            self.assertEqual(code, 0, err)
            body = json.loads(out)
            self.assertEqual(body["provenance"]["right"]["mapping"]["assign"], {"broader": "up"})
            self.assertEqual(body["classes"]["hierarchy"]["delta"]["edges"], -1)
            self.assertEqual(body["classes"]["up"]["delta"]["edges"], 1)
            code, by_file, err = run_cli(
                str(graph), str(table), "demo", "--boundary", str(other), "--format", "json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(by_file)["classes"], body["classes"])
            self.assertEqual(json.loads(by_file)["provenance"]["right"]["classification"]["path"], other.as_posix())

            code, out, err = run_cli(str(graph), str(table), "demo", "--boundary", env={
                "TOPONTOLOGY_BOUNDARY": f"  {other}  ",
            })
            self.assertEqual(code, 2)
            self.assertNotIn("Traceback", err)

            code, out, err = run_cli(str(graph), str(table), "demo", "--assign", "broader")
            self.assertEqual(code, 1)
            self.assertIn("--assign requires --split", err)
            code, out, err = run_cli(str(graph), str(table), "demo", "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("--into requires --merge", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--merge", "hierarchy", "--merge", "side")
            self.assertEqual(code, 1)
            self.assertIn("--merge requires --into", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--merge", "hierarchy", "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("two distinct classes", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--merge", "hierarchy", "--merge", "hierarchy",
                "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("two distinct classes", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "hierarchy", "--assign", "broader")
            self.assertEqual(code, 1)
            self.assertIn("RELATION=CLASS", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "hierarchy", "--assign", "=up")
            self.assertEqual(code, 1)
            self.assertIn("RELATION=CLASS", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "missing", "--assign", "broader=up")
            self.assertEqual(code, 1)
            self.assertIn("unknown class", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "hierarchy", "--assign", "narrower=hierarchy")
            self.assertEqual(code, 1)
            self.assertIn("different class", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--split", "hierarchy", "--assign", "similarTo=up")
            self.assertEqual(code, 1)
            self.assertIn("unknown relation", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo",
                "--merge", "hierarchy", "--merge", "side", "--split", "hierarchy", "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("either --merge or --split", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--anomalies",
                "--merge", "hierarchy", "--merge", "side", "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("--anomalies reads one classification", err)
            code, out, err = run_cli(
                str(graph), str(table), "demo", "--format", "mermaid", "--class", "hierarchy",
                "--boundary", str(other))
            self.assertEqual(code, 1)
            self.assertIn("mermaid", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                "--merge", "hierarchy", "--merge", "similarity",
                "--compare", "tests/fixtures/graph-extended.json", "--into", "linked")
            self.assertEqual(code, 1)
            self.assertIn("only one of", err)


if __name__ == "__main__":
    unittest.main()
