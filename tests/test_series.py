"""Snapshot series and vendor-lock graphs, including source-level mutants."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_topontology import ROOT, run_cli

CLASSES = {"narrower": "hierarchy", "similarTo": "similarity"}


def _graph(edges):
    return {"edges": edges}


def _snap(label, edges):
    return {"label": label, "path": Path(f"{label}.json"), "graph": _graph(edges)}


def load_mutant(old, new):
    """Execute a one-site edit of the real topontology.py source."""
    source = Path(topontology.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor count {source.count(old)} for {old!r}")
    spec = importlib.util.spec_from_loader("topontology_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["__file__"] = topontology.__file__
    exec(compile(source.replace(old, new, 1), topontology.__file__, "exec"), module.__dict__)
    return module


class SeriesTests(unittest.TestCase):
    def test_consecutive_pairs_match_the_hand_count(self):
        one = [{"source": "A", "kind": "narrower", "target": "B"}]
        two = one + [
            {"source": "B", "kind": "narrower", "target": "C"},
            {"source": "D", "kind": "similarTo", "target": "E"},
        ]
        three = two[1:]
        report = topontology.series_report(
            [_snap("one", one), _snap("two", two), _snap("three", three)],
            CLASSES,
        )
        first, second = report["pairs"]
        # one is a single edge (E=1,V=2,C=1,E-V+C=0). two adds a chain step and a separate edge.
        self.assertEqual(first["graph"]["delta"]["edges"], 2)
        self.assertEqual(first["graph"]["delta"]["incident_nodes"], 3)
        self.assertEqual(first["graph"]["delta"]["weakly_connected_components"], 1)
        self.assertEqual(first["graph"]["delta"]["independent_cycles"], 0)
        self.assertEqual(first["graph"]["delta"]["depth"], 1)
        self.assertEqual(first["graph"]["edges_changed"]["removed"], [])
        self.assertEqual(first["graph"]["edges_changed"]["added"], [
            {"source": "B", "relation": "narrower", "target": "C"},
            {"source": "D", "relation": "similarTo", "target": "E"},
        ])
        self.assertEqual(first["classes"]["hierarchy"]["delta"]["edges"], 1)
        self.assertEqual(first["classes"]["hierarchy"]["edges_changed"]["added"], [
            {"source": "B", "relation": "narrower", "target": "C"},
        ])
        self.assertEqual(first["classes"]["similarity"]["delta"]["edges"], 1)
        # three drops A→B, so the chain shortens by one edge and one node.
        self.assertEqual(second["graph"]["delta"]["edges"], -1)
        self.assertEqual(second["graph"]["delta"]["incident_nodes"], -1)
        self.assertEqual(second["graph"]["delta"]["weakly_connected_components"], 0)
        self.assertEqual(second["graph"]["edges_changed"]["added"], [])
        self.assertEqual(second["graph"]["edges_changed"]["removed"], [
            {"source": "A", "relation": "narrower", "target": "B"},
        ])
        self.assertEqual(second["classes"]["hierarchy"]["delta"]["edges"], -1)
        self.assertEqual(second["classes"]["similarity"]["delta"]["edges"], 0)
        by_id = {row["id"]: row for row in report["nodes"]}
        # A is present, present, absent. B stays. C, D, and E appear at two and stay.
        self.assertEqual(by_id["A"], {
            "id": "A", "first_seen": "one", "last_seen": "two", "disappeared": "three",
            "enters": 1, "leaves": 1, "churn": 2, "classes": ["hierarchy"],
        })
        self.assertEqual(by_id["B"]["churn"], 1)
        self.assertEqual(by_id["B"]["enters"], 1)
        self.assertEqual(by_id["B"]["leaves"], 0)
        self.assertIsNone(by_id["B"]["disappeared"])
        self.assertEqual(by_id["C"]["first_seen"], "two")
        self.assertEqual(by_id["C"]["classes"], ["hierarchy"])
        self.assertEqual(by_id["D"]["classes"], ["similarity"])
        self.assertEqual(by_id["E"]["classes"], ["similarity"])
        self.assertEqual([row["id"] for row in report["repeated_churn"]], ["A"])

    def test_duplicate_edge_is_counted_in_the_multiset(self):
        left = [{"source": "A", "kind": "narrower", "target": "B"}]
        right = left + left
        changed = topontology.attribute_edges(left, right)
        self.assertEqual(len(changed["added"]), 1)
        self.assertEqual(changed["removed"], [])

    def test_rejects_duplicate_labels_and_short_series(self):
        with self.assertRaises(ValueError) as raised:
            topontology.series_report([_snap("only", [])], CLASSES)
        self.assertEqual(str(raised.exception), "series needs at least 2 snapshots")
        with self.assertRaises(ValueError) as raised:
            topontology.series_report([_snap("same", []), _snap("same", [])], CLASSES)
        self.assertEqual(str(raised.exception), "duplicate snapshot label: same")


class VendorLockTests(unittest.TestCase):
    def test_synthetic_lock_becomes_vendors_from_edges(self):
        document = json.loads((ROOT / "tests/fixtures/vendor-lock.json").read_text(encoding="utf-8"))
        graph = topontology.graph_from_vendor_lock(document)
        self.assertEqual(graph["edges"], [
            {"source": "vendor/lib.py", "relation": "vendorsFrom", "target": "example/upstream:lib.py"},
            {"source": "vendor/other.py", "relation": "vendorsFrom", "target": "example/upstream:other.py"},
        ])
        self.assertEqual(
            [node["id"] for node in graph["nodes"]],
            [
                "example/upstream:lib.py",
                "example/upstream:other.py",
                "vendor/lib.py",
                "vendor/other.py",
            ],
        )
        # Two files, two distinct upstreams: E=2, V=4, C=2, E-V+C=0.
        metrics = topontology._class_metrics(graph["edges"], 5)
        self.assertEqual(metrics["edges"], 2)
        self.assertEqual(metrics["incident_nodes"], 4)
        self.assertEqual(metrics["weakly_connected_components"]["count"], 2)
        self.assertEqual(metrics["independent_cycles"], 0)

    def test_path_and_repository_only_forms(self):
        graph = topontology.graph_from_vendor_lock({
            "schema": "vendor-lock/1",
            "files": [
                {"path": "vendor/local.py", "source": "example/origin"},
                {"destination": "vendor/repo.py", "repository": "example/repo"},
            ],
        })
        self.assertEqual(graph["edges"], [
            {"source": "vendor/local.py", "relation": "vendorsFrom", "target": "example/origin"},
            {"source": "vendor/repo.py", "relation": "vendorsFrom", "target": "example/repo"},
        ])

    def test_unknown_schema_and_bad_entries_fail(self):
        with self.assertRaises(ValueError) as raised:
            topontology.graph_from_vendor_lock({"schema": "vendor-lock/2", "files": []})
        self.assertIn("unknown vendor-lock schema", str(raised.exception))
        with self.assertRaises(ValueError):
            topontology.graph_from_vendor_lock(["nope"])
        with self.assertRaises(ValueError):
            topontology.graph_from_vendor_lock({"schema": "vendor-lock/1"})
        with self.assertRaises(ValueError):
            topontology.graph_from_vendor_lock({"schema": "vendor-lock/1", "files": ["x"]})
        with self.assertRaises(ValueError):
            topontology.graph_from_vendor_lock({
                "schema": "vendor-lock/1",
                "files": [{"repository": "example/repo"}],
            })
        with self.assertRaises(ValueError):
            topontology.graph_from_vendor_lock({
                "schema": "vendor-lock/1",
                "files": [{"destination": "vendor/x.py"}],
            })

    def test_non_string_repository_or_source_is_an_error(self):
        valid = {"destination": "vendor/x.py", "repository": "example/repo"}
        with self.assertRaises(ValueError) as raised:
            topontology.graph_from_vendor_lock({
                "schema": "vendor-lock/1",
                "files": [{**valid, "source": 1}],
            })
        self.assertIn("source must be a string", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            topontology.graph_from_vendor_lock({
                "schema": "vendor-lock/1",
                "files": [{**valid, "source": ["lib.py"]}],
            })
        self.assertIn("source must be a string", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            topontology.graph_from_vendor_lock({
                "schema": "vendor-lock/1",
                "files": [{"destination": "vendor/x.py", "source": "lib.py", "repository": 2}],
            })
        self.assertIn("repository must be a string", str(raised.exception))
        # Missing keys are still allowed; only a present non-string is rejected.
        graph = topontology.graph_from_vendor_lock({
            "schema": "vendor-lock/1",
            "files": [{"destination": "vendor/x.py", "repository": "example/repo"}],
        })
        self.assertEqual(graph["edges"][0]["target"], "example/repo")
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "lock.json"
            lock.write_text(json.dumps({
                "schema": "vendor-lock/1",
                "files": [{"destination": "vendor/x.py", "repository": "example/repo", "source": 1}],
            }), encoding="utf-8")
            code, out, err = run_cli(
                str(lock), "tests/fixtures/vendor-classes.json", "demo",
                "--graph-format", "vendor-lock")
            self.assertEqual(code, 1)
            self.assertIn("source must be a string", err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(out, "")


class SeriesCliTests(unittest.TestCase):
    def test_series_matches_committed_output(self):
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
            "--snapshot", "three=tests/fixtures/series-three.json",
            "--format", "canonical",
        )
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/series.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(body["tool"]["version"], topontology.__version__)
        self.assertEqual([item["label"] for item in body["provenance"]["snapshots"]], ["one", "two", "three"])
        self.assertEqual(body["pairs"][0]["graph"]["delta"]["independent_cycles"], 0)
        self.assertEqual(body["pairs"][0]["graph"]["delta"]["weakly_connected_components"], 1)
        code, text, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
            "--snapshot", "three=tests/fixtures/series-three.json",
            "--format", "text",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("pair one -> two:", text)
        self.assertIn("added: B narrower C, D similarTo E", text)
        self.assertIn("removed: (none)", text)
        self.assertIn("added: (none)", text)

    def test_args_override_env_and_whitespace_is_stripped(self):
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "  one = tests/fixtures/series-one.json  ",
            "--snapshot", "two=tests/fixtures/series-two.json",
            env={
                "TOPONTOLOGY_CLASSIFICATION": "/missing/table.json",
                "TOPONTOLOGY_PROFILE": "missing",
                "TOPONTOLOGY_SNAPSHOTS": "ignored=tests/fixtures/series-three.json,also=tests/fixtures/series-one.json",
            },
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual([item["label"] for item in body["provenance"]["snapshots"]], ["one", "two"])
        self.assertEqual(body["provenance"]["profile"], "demo")
        code, out, err = run_cli(
            "series",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
            env={"TOPONTOLOGY_PROFILE": "demo", "TOPONTOLOGY_CLASSIFICATION": "tests/fixtures/classes.json"},
        )
        self.assertEqual(code, 1)
        self.assertIn("pass the profile", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli("series", "tests/fixtures/classes.json", "demo", env={
            "TOPONTOLOGY_SNAPSHOTS": "one=tests/fixtures/series-one.json\ntwo=tests/fixtures/series-two.json",
        })
        self.assertEqual(code, 1)
        self.assertEqual(err, "series needs at least 2 snapshots\n")
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "same=tests/fixtures/series-one.json",
            "--snapshot", "same=tests/fixtures/series-two.json",
        )
        self.assertEqual(code, 1)
        self.assertEqual(err, "duplicate snapshot label: same\n")
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "only=tests/fixtures/series-one.json",
        )
        self.assertEqual(code, 1)
        self.assertIn("at least 2 snapshots", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "broken",
            "--snapshot", "two=tests/fixtures/series-two.json",
        )
        self.assertEqual(code, 1)
        self.assertIn("LABEL=PATH", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
        )
        self.assertEqual(code, 1)
        self.assertIn("LABEL=PATH", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "one=",
            "--snapshot", "two=tests/fixtures/series-two.json",
        )
        self.assertEqual(code, 1)
        self.assertIn("LABEL=PATH", err)
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "   ",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual([item["label"] for item in json.loads(out)["provenance"]["snapshots"]], ["one", "two"])
        # Whitespace-only --snapshot arguments must not fall back to the env var.
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "   ",
            env={
                "TOPONTOLOGY_SNAPSHOTS": "one=tests/fixtures/series-one.json,two=tests/fixtures/series-two.json",
            },
        )
        self.assertEqual(code, 1)
        self.assertIn("LABEL=PATH", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        with self.assertRaises(ValueError) as raised:
            topontology._resolve_snapshots(["   ", "\t"])
        self.assertIn("LABEL=PATH", str(raised.exception))
        code, out, err = run_cli(
            "series", "tests/fixtures/classes.json", "demo",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
            "--cycle-limit", "-1",
        )
        self.assertEqual(code, 1)
        self.assertIn("--cycle-limit", err)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "series.json"
            code, out, err = run_cli(
                "series", "tests/fixtures/classes.json", "demo",
                "--snapshot", "one=tests/fixtures/series-one.json",
                "--snapshot", "two=tests/fixtures/series-two.json",
                "-o", str(dest),
                env={"TOPONTOLOGY_SNAPSHOTS": "one=tests/fixtures/series-one.json, ,\ntwo=tests/fixtures/series-two.json"},
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertIn('"pairs"', dest.read_text(encoding="utf-8"))
        code, out, err = run_cli(
            "series", "demo",
            "--classification", "tests/fixtures/classes.json",
            "--snapshot", "one=tests/fixtures/series-one.json",
            "--snapshot", "two=tests/fixtures/series-two.json",
            env={"TOPONTOLOGY_CLASSIFICATION": "/missing/table.json"},
        )
        self.assertEqual(code, 0, err)
        self.assertEqual([item["label"] for item in json.loads(out)["provenance"]["snapshots"]], ["one", "two"])
        self.assertTrue(json.loads(out)["provenance"]["classification"]["path"].endswith("classes.json"))
        with self.assertRaises(ValueError):
            topontology._load_graph(ROOT / "tests/fixtures/graph.json", "nope")

    def test_vendor_lock_cli_matches_committed_output(self):
        code, out, err = run_cli(
            "tests/fixtures/vendor-lock.json", "tests/fixtures/vendor-classes.json", "demo",
            "--graph-format", "vendor-lock", "--format", "canonical",
        )
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/vendor-lock.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(body["classes"]["dependency"]["edges"], 2)
        self.assertEqual(body["classes"]["dependency"]["independent_cycles"], 0)
        code, out, err = run_cli(
            "tests/fixtures/vendor-lock.json", "tests/fixtures/vendor-classes.json", "demo",
            "--graph-format", "vendor-lock",
            env={"TOPONTOLOGY_GRAPH": "tests/fixtures/graph.json"},
        )
        self.assertEqual(code, 0, err)
        code, out, err = run_cli("tests/fixtures/classes.json", "tests/fixtures/vendor-classes.json", "demo",
                                 "--graph-format", "vendor-lock")
        self.assertEqual(code, 1)
        self.assertIn("unknown vendor-lock schema", err)
        self.assertNotIn("Traceback", err)


class SourceMutationTests(unittest.TestCase):
    def test_swapped_edge_attribution_is_killed(self):
        mutant = load_mutant(
            'return {"added": added, "removed": removed}',
            'return {"added": removed, "removed": added}',
        )
        left = [{"source": "A", "kind": "narrower", "target": "B"}]
        right = left + [{"source": "B", "kind": "narrower", "target": "C"}]
        real = topontology.attribute_edges(left, right)
        changed = mutant.attribute_edges(left, right)
        self.assertNotEqual(real["added"], changed["added"])
        self.assertEqual(changed["added"], [])
        self.assertEqual(real["added"][0]["target"], "C")

    def test_renamed_vendor_relation_is_killed(self):
        mutant = load_mutant('relation": _VENDOR_RELATION', 'relation": "vendorsTo"')
        document = {"schema": "vendor-lock/1", "files": [
            {"destination": "vendor/lib.py", "repository": "example/upstream", "source": "lib.py"},
        ]}
        real = topontology.graph_from_vendor_lock(document)["edges"][0]["relation"]
        changed = mutant.graph_from_vendor_lock(document)["edges"][0]["relation"]
        self.assertEqual(real, "vendorsFrom")
        self.assertEqual(changed, "vendorsTo")
        self.assertNotEqual(real, changed)

    def test_dropped_duplicate_label_check_is_killed(self):
        mutant = load_mutant(
            'if label in seen:\n            raise ValueError(f"duplicate snapshot label: {label}")',
            'if False and label in seen:\n            raise ValueError(f"duplicate snapshot label: {label}")',
        )
        snapshots = [_snap("same", []), _snap("same", [])]
        with self.assertRaises(ValueError):
            topontology.series_report(snapshots, CLASSES)
        report = mutant.series_report(snapshots, CLASSES)
        self.assertEqual(report["pairs"][0]["left"], "same")
        self.assertEqual(report["pairs"][0]["right"], "same")

    def test_swapped_enter_and_leave_is_killed(self):
        mutant = load_mutant(
            "            if flag and not previous:\n"
            "                enters += 1\n"
            "            elif previous and not flag:\n"
            "                leaves += 1\n",
            "            if flag and not previous:\n"
            "                leaves += 1\n"
            "            elif previous and not flag:\n"
            "                enters += 1\n",
        )
        stayed = [_snap("one", [{"source": "A", "kind": "narrower", "target": "B"}]),
                  _snap("two", [{"source": "A", "kind": "narrower", "target": "B"}])]
        real = topontology.node_churn(stayed, CLASSES)["nodes"]
        changed = mutant.node_churn(stayed, CLASSES)["nodes"]
        self.assertEqual({row["id"]: (row["enters"], row["leaves"]) for row in real}, {"A": (1, 0), "B": (1, 0)})
        self.assertEqual({row["id"]: (row["enters"], row["leaves"]) for row in changed}, {"A": (0, 1), "B": (0, 1)})

    def test_dropped_disappeared_point_is_killed(self):
        mutant = load_mutant(
            "disappeared = None if seen[-1] else labels[last + 1]",
            "disappeared = None",
        )
        snapshots = [
            _snap("one", [{"source": "A", "kind": "narrower", "target": "B"}]),
            _snap("two", []),
        ]
        real = topontology.node_churn(snapshots, CLASSES)["nodes"]
        changed = mutant.node_churn(snapshots, CLASSES)["nodes"]
        self.assertEqual(real[0]["disappeared"], "two")
        self.assertIsNone(changed[0]["disappeared"])

    def test_ascending_churn_sort_is_killed(self):
        mutant = load_mutant(
            'repeated.sort(key=lambda row: (-row["churn"], node_sort_key(row["id"])))',
            'repeated.sort(key=lambda row: (row["churn"], node_sort_key(row["id"])))',
        )
        quiet = [{"source": "A", "kind": "narrower", "target": "B"}]
        busy = [{"source": "C", "kind": "narrower", "target": "D"}]
        snapshots = [_snap("one", quiet + busy), _snap("two", quiet), _snap("three", quiet + busy), _snap("four", [])]
        real = [row["id"] for row in topontology.node_churn(snapshots, CLASSES)["repeated_churn"]]
        changed = [row["id"] for row in mutant.node_churn(snapshots, CLASSES)["repeated_churn"]]
        # C and D leave, return, and leave again (churn 4). A and B leave once (churn 2).
        self.assertEqual(real[:2], ["C", "D"])
        self.assertEqual(changed[:2], ["A", "B"])


class ChurnTests(unittest.TestCase):
    def test_reentry_tie_break_and_unclassified_classes(self):
        snapshots = [
            _snap("t0", [
                {"source": 1, "kind": "narrower", "target": "b"},
                {"source": "X", "relation": "hasName", "target": "Y"},
            ]),
            _snap("t1", [{"source": "X", "relation": "hasName", "target": "Y"}]),
            _snap("t2", [
                {"source": 1, "kind": "narrower", "target": "b"},
                {"source": "a", "kind": "similarTo", "target": "b"},
                {"source": "X", "relation": "hasName", "target": "Y"},
            ]),
            _snap("t3", []),
        ]
        report = topontology.node_churn(snapshots, CLASSES)
        by_id = {row["id"]: row for row in report["nodes"]}
        self.assertEqual(by_id[1]["churn"], 4)
        self.assertEqual(by_id[1]["enters"], 2)
        self.assertEqual(by_id[1]["leaves"], 2)
        self.assertEqual(by_id[1]["first_seen"], "t0")
        self.assertEqual(by_id[1]["last_seen"], "t2")
        self.assertEqual(by_id[1]["disappeared"], "t3")
        self.assertEqual(by_id[1]["classes"], ["hierarchy"])
        self.assertEqual(by_id["b"]["classes"], ["hierarchy", "similarity"])
        self.assertEqual(by_id["b"]["churn"], 4)
        self.assertEqual(by_id["a"]["churn"], 2)
        self.assertEqual(by_id["a"]["first_seen"], "t2")
        self.assertEqual(by_id["a"]["classes"], ["similarity"])
        # X stays through t2 on an unclassified edge, then leaves. No class is recorded.
        self.assertEqual(by_id["X"]["classes"], [])
        self.assertEqual(by_id["X"]["churn"], 2)
        self.assertEqual(by_id["X"]["disappeared"], "t3")
        self.assertEqual(by_id["Y"]["classes"], [])
        # int 1 sorts before str "b" at the same churn. Among churn 2, "X" < "Y" < "a".
        self.assertEqual([row["id"] for row in report["repeated_churn"]], [1, "b", "X", "Y", "a"])
        empty = topontology.node_churn([_snap("one", []), _snap("two", [])], CLASSES)
        self.assertEqual(empty, {"nodes": [], "repeated_churn": []})
        text = topontology._series_text({
            "tool": {"name": "topontology", "version": topontology.__version__},
            "provenance": {"classification": {"path": "c", "sha256": "0"}, "profile": "demo", "snapshots": []},
            "pairs": [],
            "nodes": [],
            "repeated_churn": [],
        })
        self.assertIn("nodes:\n  (none)", text)
        self.assertIn("repeated_churn:\n  (none)", text)


class AoiLockHistoryTests(unittest.TestCase):
    def test_fifteen_blobs_seven_graphs_and_no_reentry(self):
        history = ROOT / "tests/fixtures/aoi-history"
        provenance = json.loads((history / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["commits_touching_path"], 15)
        self.assertEqual(provenance["unique_blobs"], 15)
        self.assertEqual(provenance["unique_graphs"], 7)
        self.assertEqual(len(provenance["blobs"]), 15)
        self.assertEqual(len({item["blob"] for item in provenance["blobs"]}), 15)
        self.assertEqual(provenance["main"], "320126983a992e30730350c5e9b0e4051c0d33d4")
        self.assertEqual(provenance["main_blob"], provenance["blobs"][-1]["blob"])
        self.assertEqual(provenance["main_blob"], "644535e13ddbe5639bd6d0f93e2c4ba97d1d208e")
        snapshots = []
        for item in provenance["graphs"]:
            path = history / item["fixture"]
            snapshots.append({
                "label": item["commit"],
                "path": path,
                "graph": topontology.graph_from_vendor_lock(json.loads(path.read_text(encoding="utf-8"))),
            })
        report = topontology.series_report(snapshots, {"vendorsFrom": "dependency"})
        by_id = {row["id"]: row for row in report["nodes"]}
        rename = "b4d91f117729396c9443a7f86f587ec46f6347f7"
        drop = "014afdc6063b8db17135731b24c624e19d3c410f"
        stayed = by_id["vendor/markdown.py"]
        self.assertEqual(stayed["first_seen"], provenance["graphs"][0]["commit"])
        self.assertEqual(stayed["last_seen"], provenance["graphs"][-1]["commit"])
        self.assertIsNone(stayed["disappeared"])
        self.assertEqual(stayed["enters"], 1)
        self.assertEqual(stayed["leaves"], 0)
        self.assertEqual(stayed["classes"], ["dependency"])
        local = by_id["vendor/web_ui.py"]
        self.assertEqual(local["disappeared"], drop)
        self.assertEqual(local["churn"], 2)
        self.assertEqual(local["enters"], 1)
        old = by_id["myon-bioinformatics/web-ui:web_ui.py"]
        self.assertEqual(old["disappeared"], rename)
        new = by_id["myon-bioinformatics/PySaiyanWeBoo:web_ui.py"]
        self.assertEqual(new["first_seen"], rename)
        self.assertEqual(new["disappeared"], drop)
        self.assertTrue(all(row["enters"] == 1 for row in report["nodes"]))
        self.assertTrue(all(row["churn"] == 2 for row in report["repeated_churn"]))
        self.assertEqual([row["id"] for row in report["repeated_churn"]], [
            "myon-bioinformatics/PySaiyanWeBoo:LICENSE",
            "myon-bioinformatics/PySaiyanWeBoo:web_ui.py",
            "myon-bioinformatics/web-ui:LICENSE",
            "myon-bioinformatics/web-ui:web_ui.py",
            "vendor/web-ui-LICENSE",
            "vendor/web_ui.py",
        ])
        args = ["series", "tests/fixtures/vendor-classes.json", "demo", "--graph-format", "vendor-lock", "--format", "canonical"]
        for item in provenance["graphs"]:
            args.extend(["--snapshot", f"{item['commit']}=tests/fixtures/aoi-history/{item['fixture']}"])
        code, out, err = run_cli(*args)
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/series-locks.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        body = json.loads(out)
        self.assertEqual(len(body["provenance"]["snapshots"]), 7)
        self.assertEqual(body["repeated_churn"][0]["churn"], 2)
        mixed = topontology._churn_lines({
            "nodes": [{
                "id": 1, "first_seen": "t0", "last_seen": "t0", "disappeared": None,
                "enters": 1, "leaves": 0, "churn": 1, "classes": [],
            }],
            "repeated_churn": [],
        })
        self.assertIn("1 first_seen=t0", "\n".join(mixed))
        self.assertIn("classes=(none)", "\n".join(mixed))
        self.assertIn("disappeared=(none)", "\n".join(mixed))


if __name__ == "__main__":
    unittest.main()
