"""Reverse reachability of vendor-lock/1 fleets, including the Aoi snapshot."""
import json
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_series import load_mutant
from test_topontology import ROOT, run_cli

AOI = ROOT / "tests/fixtures/aoi-locks"
COMMIT = "320126983a992e30730350c5e9b0e4051c0d33d4"

# Hand count of the copied Aoi locks. downstream_repos, then relay repositories.
AOI_RANK = [
    ("myon-bioinformatics/myon-bioinformatics:fleet_check.py", 6, []),
    ("myon-bioinformatics/myon-bioinformatics:py_launch.cjs", 4, ["myon-bioinformatics/Aoi"]),
    ("myon-bioinformatics/Aoi:vendor/py_launch.cjs", 3, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/numpy_min.py", 3, ["myon-bioinformatics/Aoi"]),
    ("myon-bioinformatics/Aoi:shrink/numpy_min.py", 2, []),
    ("myon-bioinformatics/markdown:markdown.py", 2, []),
    ("myon-bioinformatics/Aoi:LICENSE", 1, []),
    ("myon-bioinformatics/markdown:LICENSE", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/biopython_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/httpx_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/numpy_linalg_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/plotly_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/polars_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/requests_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/tqdm_min.py", 1, []),
    ("myon-bioinformatics/myon-bioinformatics:tools/uv_tool_build.py", 1, []),
]


def _lock(repository, files):
    entries = []
    for item in files:
        entry = {"destination": item["destination"]}
        if "repository" in item:
            entry["repository"] = item["repository"]
        if "source" in item:
            entry["source"] = item["source"]
        entries.append(entry)
    return {"repository": repository, "document": {"schema": "vendor-lock/1", "files": entries}}


def _edge(destination, repository, source):
    return {"destination": destination, "repository": repository, "source": source}


def _rank(report):
    return [
        (row["id"], row["downstream_repos"], row["relays"])
        for row in report["sources"]
    ]


def _aoi_locks():
    provenance = json.loads((AOI / "provenance.json").read_text(encoding="utf-8"))
    locks = []
    for item in provenance["locks"]:
        locks.append({
            "repository": item["repository"],
            "document": json.loads((AOI / item["fixture"]).read_text(encoding="utf-8")),
        })
    return locks


def _aoi_cli_args():
    provenance = json.loads((AOI / "provenance.json").read_text(encoding="utf-8"))
    args = ["impact"]
    for item in provenance["locks"]:
        args.extend(["--lock", f"{item['repository']}=tests/fixtures/aoi-locks/{item['fixture']}"])
    return args


class ImpactTests(unittest.TestCase):
    def test_chain_depth_relay_and_same_repo(self):
        report = topontology.impact_report([
            _lock("origin", [_edge("lib.py", "upstream", "lib.py")]),
            _lock("mid", [_edge("vendor/lib.py", "origin", "lib.py")]),
            _lock("leaf", [_edge("vendor/mid.py", "mid", "vendor/lib.py")]),
        ])
        by_id = {row["id"]: row for row in report["sources"]}
        root = by_id["upstream:lib.py"]
        self.assertEqual(root["downstream_repos"], 3)
        self.assertEqual(root["reached"], [
            {"repository": "origin", "depth": 1},
            {"repository": "mid", "depth": 2},
            {"repository": "leaf", "depth": 3},
        ])
        self.assertEqual(root["relays"], ["mid", "origin"])
        self.assertEqual(by_id["origin:lib.py"]["downstream_repos"], 2)
        self.assertEqual(by_id["origin:lib.py"]["relays"], ["mid"])
        self.assertEqual(by_id["mid:vendor/lib.py"]["reached"], [
            {"repository": "leaf", "depth": 1},
        ])
        self.assertEqual(by_id["mid:vendor/lib.py"]["relays"], [])
        self.assertTrue(report["policy"]["holds"])
        self.assertEqual(report["policy"]["name"], "single-canonical-source")
        self.assertEqual(report["policy"]["violations"], [])

    def test_same_repo_continuation_is_not_a_relay(self):
        report = topontology.impact_report([
            _lock("child", [
                _edge("a.py", "upstream", "a.py"),
                _edge("b.py", "child", "a.py"),
            ]),
        ])
        row = next(item for item in report["sources"] if item["id"] == "upstream:a.py")
        self.assertEqual(row["downstream_repos"], 1)
        self.assertEqual(row["reached"], [{"repository": "child", "depth": 1}])
        self.assertEqual(row["relays"], [])
        self.assertTrue(report["policy"]["holds"])

    def test_shortest_hop_and_duplicate_edge(self):
        report = topontology.impact_report([
            _lock("direct", [
                _edge("near.py", "upstream", "root.py"),
                _edge("also.py", "upstream", "root.py"),
            ]),
            _lock("side", [_edge("side.py", "upstream", "root.py")]),
            _lock("far", [
                _edge("far.py", "direct", "near.py"),
                _edge("far.py", "side", "side.py"),
            ]),
        ])
        row = next(item for item in report["sources"] if item["id"] == "upstream:root.py")
        self.assertEqual(row["downstream_repos"], 3)
        self.assertEqual(row["reached"], [
            {"repository": "direct", "depth": 1},
            {"repository": "side", "depth": 1},
            {"repository": "far", "depth": 2},
        ])
        self.assertEqual(row["relays"], ["direct", "side"])
        self.assertTrue(report["policy"]["holds"])

    def test_repository_only_upstream_has_no_source_field(self):
        report = topontology.impact_report([
            _lock("child", [{"destination": "local.py", "repository": "example/origin"}]),
        ])
        row = report["sources"][0]
        self.assertEqual(row["id"], "example/origin")
        self.assertEqual(row["repository"], "example/origin")
        self.assertEqual(row["source"], "")
        self.assertEqual(row["downstream_repos"], 1)
        self.assertTrue(report["policy"]["holds"])

    def test_two_roots_are_a_policy_violation(self):
        report = topontology.impact_report([
            _lock("child", [
                _edge("x.py", "one", "a.py"),
                _edge("x.py", "two", "b.py"),
            ]),
        ])
        self.assertFalse(report["policy"]["holds"])
        self.assertEqual(report["policy"]["violations"], [{
            "repository": "child",
            "path": "x.py",
            "roots": ["one:a.py", "two:b.py"],
            "cycle": False,
        }])

    def test_two_paths_to_one_root_still_hold(self):
        report = topontology.impact_report([
            _lock("left", [_edge("m.py", "root", "r.py")]),
            _lock("right", [_edge("n.py", "root", "r.py")]),
            _lock("child", [
                _edge("c.py", "left", "m.py"),
                _edge("c.py", "right", "n.py"),
            ]),
        ])
        self.assertTrue(report["policy"]["holds"])
        root = next(item for item in report["sources"] if item["id"] == "root:r.py")
        self.assertEqual(root["downstream_repos"], 3)
        self.assertEqual(root["relays"], ["left", "right"])

    def test_cycle_has_no_canonical_root(self):
        report = topontology.impact_report([
            _lock("alpha", [_edge("a.py", "beta", "b.py")]),
            _lock("beta", [_edge("b.py", "alpha", "a.py")]),
            _lock("clean", [_edge("c.py", "root", "c.py")]),
        ])
        self.assertFalse(report["policy"]["holds"])
        by_path = {(item["repository"], item["path"]): item for item in report["policy"]["violations"]}
        self.assertEqual(set(by_path), {("alpha", "a.py"), ("beta", "b.py")})
        self.assertEqual(by_path[("alpha", "a.py")]["roots"], [])
        self.assertTrue(by_path[("alpha", "a.py")]["cycle"])
        self.assertTrue(by_path[("beta", "b.py")]["cycle"])

    def test_self_loop_is_a_cycle(self):
        report = topontology.impact_report([
            _lock("loop", [_edge("a.py", "loop", "a.py")]),
        ])
        self.assertEqual(report["policy"]["violations"], [{
            "repository": "loop",
            "path": "a.py",
            "roots": [],
            "cycle": True,
        }])

    def test_empty_lock_has_no_sources(self):
        report = topontology.impact_report([
            {"repository": "child", "document": {"schema": "vendor-lock/1", "files": []}},
        ])
        self.assertEqual(report["sources"], [])
        self.assertTrue(report["policy"]["holds"])

    def test_rejects_no_locks_and_duplicate_repositories(self):
        with self.assertRaises(ValueError) as raised:
            topontology.impact_report([])
        self.assertEqual(str(raised.exception), "impact needs at least 1 lock")
        with self.assertRaises(ValueError) as raised:
            topontology.impact_report([
                _lock("same", [_edge("a.py", "up", "a.py")]),
                _lock("same", [_edge("b.py", "up", "b.py")]),
            ])
        self.assertEqual(str(raised.exception), "duplicate lock repository: same")


class AoiImpactTests(unittest.TestCase):
    def test_fixtures_keep_the_real_lock_shape(self):
        provenance = json.loads((AOI / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["copied_from"]["repository"], "myon-bioinformatics/Aoi")
        self.assertEqual(provenance["copied_from"]["commit"], COMMIT)
        self.assertEqual(provenance["copied_from"]["kept"], [
            "schema", "files[].repository", "files[].source", "files[].destination",
        ])
        seen = set()
        for item in provenance["locks"]:
            document = json.loads((AOI / item["fixture"]).read_text(encoding="utf-8"))
            self.assertEqual(document["schema"], "vendor-lock/1")
            self.assertEqual(set(document), {"schema", "files"})
            for entry in document["files"]:
                self.assertEqual(set(entry), {"repository", "source", "destination"})
            seen.add(item["repository"])
        self.assertEqual(seen, {item["repository"] for item in provenance["locks"]})
        queryu = json.loads((AOI / "queryu.json").read_text(encoding="utf-8"))
        self.assertIn(
            {"repository": "myon-bioinformatics/Aoi", "source": "shrink/numpy_min.py",
             "destination": "vendor/aoi/shrink/numpy_min.py"},
            queryu["files"],
        )

    def test_aoi_ranking_matches_the_hand_count(self):
        report = topontology.impact_report(_aoi_locks())
        self.assertEqual(_rank(report), AOI_RANK)
        by_id = {row["id"]: row for row in report["sources"]}
        fleet = by_id["myon-bioinformatics/myon-bioinformatics:fleet_check.py"]
        self.assertEqual([item["depth"] for item in fleet["reached"]], [1, 1, 1, 1, 1, 1])
        self.assertEqual([item["repository"] for item in fleet["reached"]], [
            "myon-bioinformatics/BlueProbe",
            "myon-bioinformatics/DRAgoWing",
            "myon-bioinformatics/NagoyAction",
            "myon-bioinformatics/PythDRagoras",
            "myon-bioinformatics/QueRyu",
            "myon-bioinformatics/SakAnalytics",
        ])
        launch = by_id["myon-bioinformatics/myon-bioinformatics:py_launch.cjs"]
        self.assertEqual(launch["reached"], [
            {"repository": "myon-bioinformatics/Aoi", "depth": 1},
            {"repository": "myon-bioinformatics/NagoyAction", "depth": 2},
            {"repository": "myon-bioinformatics/PythDRagoras", "depth": 2},
            {"repository": "myon-bioinformatics/SakAnalytics", "depth": 2},
        ])
        numpy = by_id["myon-bioinformatics/myon-bioinformatics:tools/stdlib_min/numpy_min.py"]
        self.assertEqual(numpy["reached"], [
            {"repository": "myon-bioinformatics/Aoi", "depth": 1},
            {"repository": "myon-bioinformatics/DRAgoWing", "depth": 2},
            {"repository": "myon-bioinformatics/QueRyu", "depth": 2},
        ])
        self.assertTrue(report["policy"]["holds"])
        self.assertEqual(report["sources"][0]["downstream_repos"], 6)


class ImpactCliTests(unittest.TestCase):
    def test_canonical_output_and_text(self):
        code, out, err = run_cli(*_aoi_cli_args(), "--format", "canonical")
        self.assertEqual(code, 0, err)
        expected = (ROOT / "tests/expected/impact.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        code, json_out, err = run_cli(*_aoi_cli_args(), "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json_out, expected)
        code, text, err = run_cli(*_aoi_cli_args(), "--format", "text")
        self.assertEqual(code, 0, err)
        self.assertIn(f"tool: topontology {topontology.__version__}", text)
        self.assertIn(
            "source myon-bioinformatics/myon-bioinformatics:fleet_check.py "
            "downstream_repos=6 relays=(none)",
            text,
        )
        self.assertIn(
            "source myon-bioinformatics/myon-bioinformatics:py_launch.cjs "
            "downstream_repos=4 relays=myon-bioinformatics/Aoi",
            text,
        )
        self.assertIn("depth 1: myon-bioinformatics/Aoi", text)
        self.assertIn("policy: single-canonical-source holds", text)
        self.assertIn(f"sha256:{topontology.file_record(AOI / 'aoi.json')['sha256']}", text)

    def test_lock_order_does_not_change_the_document(self):
        forward = _aoi_cli_args()
        backward = ["impact"]
        locks = list(zip(forward[1::2], forward[2::2]))
        for flag, value in reversed(locks):
            backward.extend([flag, value])
        code, left, err = run_cli(*forward, "--format", "canonical")
        self.assertEqual(code, 0, err)
        code, right, err = run_cli(*backward, "--format", "json")
        self.assertEqual(code, 0, err)
        self.assertEqual(left, right)

    def test_args_override_env_and_errors_stay_one_line(self):
        code, out, err = run_cli(
            "impact",
            "--lock", "  child = tests/fixtures/aoi-locks/blueprobe.json  ",
            env={"TOPONTOLOGY_LOCKS": "ignored=tests/fixtures/aoi-locks/aoi.json"},
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual([item["repository"] for item in body["provenance"]["locks"]], [
            "child",
        ])
        self.assertEqual(body["sources"][0]["downstream_repos"], 1)
        code, out, err = run_cli("impact", env={
            "TOPONTOLOGY_LOCKS": (
                "myon-bioinformatics/BlueProbe=tests/fixtures/aoi-locks/blueprobe.json, ,\n"
                "myon-bioinformatics/Aoi=tests/fixtures/aoi-locks/aoi.json"
            ),
        })
        self.assertEqual(code, 1)
        self.assertIn("at least 1 lock", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        code, out, err = run_cli("impact", env={"TOPONTOLOGY_LOCKS": "   \n  "})
        self.assertEqual(code, 1)
        self.assertEqual(err, "impact needs at least 1 lock\n")
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        code, out, err = run_cli("impact")
        self.assertEqual(code, 1)
        self.assertIn("at least 1 lock", err)
        code, out, err = run_cli("impact", "--lock", "   ", env={
            "TOPONTOLOGY_LOCKS": "child=tests/fixtures/aoi-locks/blueprobe.json",
        })
        self.assertEqual(code, 1)
        self.assertIn("REPOSITORY=PATH", err)
        self.assertNotIn("Traceback", err)
        for token in ("broken", "=tests/fixtures/aoi-locks/blueprobe.json", "child="):
            code, out, err = run_cli("impact", "--lock", token)
            self.assertEqual(code, 1)
            self.assertIn("REPOSITORY=PATH", err)
            self.assertEqual(out, "")
        code, out, err = run_cli(
            "impact",
            "--lock", "same=tests/fixtures/aoi-locks/blueprobe.json",
            "--lock", "same=tests/fixtures/aoi-locks/aoi.json",
        )
        self.assertEqual(code, 1)
        self.assertEqual(err, "duplicate lock repository: same\n")
        code, out, err = run_cli("impact", "--lock", "child=tests/fixtures/missing-lock.json")
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli("impact", "--lock", "child=tests/fixtures/classes.json")
        self.assertEqual(code, 1)
        self.assertIn("unknown vendor-lock schema", err)
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "impact", "--lock", "child=tests/fixtures/aoi-locks/blueprobe.json",
            "--format", "text", "--json")
        self.assertEqual(code, 1)
        self.assertIn("--json", err)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "impact.txt"
            code, out, err = run_cli(
                "impact", "--lock", "child=tests/fixtures/aoi-locks/blueprobe.json",
                "--format", "text", "-o", str(dest),
            )
            self.assertEqual(code, 0, err)
            self.assertEqual(out, "")
            self.assertIn("downstream_repos=1", dest.read_text(encoding="utf-8"))
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            code, out, err = run_cli("impact", "--lock", f"child={bad}")
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps({"schema": "vendor-lock/1", "files": []}), encoding="utf-8")
            code, out, err = run_cli("impact", "--lock", f"child={empty}", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("sources: (none)", out)
            self.assertIn("policy: single-canonical-source holds", out)
            cycle_a = Path(tmp) / "a.json"
            cycle_b = Path(tmp) / "b.json"
            cycle_a.write_text(json.dumps({
                "schema": "vendor-lock/1",
                "files": [_edge("a.py", "beta", "b.py")],
            }), encoding="utf-8")
            cycle_b.write_text(json.dumps({
                "schema": "vendor-lock/1",
                "files": [_edge("b.py", "alpha", "a.py")],
            }), encoding="utf-8")
            code, out, err = run_cli(
                "impact", "--lock", f"alpha={cycle_a}", "--lock", f"beta={cycle_b}",
                "--format", "text",
            )
            self.assertEqual(code, 0, err)
            self.assertIn("policy: single-canonical-source violations=2", out)
            self.assertIn("roots=(none) cycle=yes", out)
            split = Path(tmp) / "split.json"
            split.write_text(json.dumps({
                "schema": "vendor-lock/1",
                "files": [_edge("x.py", "one", "a.py"), _edge("x.py", "two", "b.py")],
            }), encoding="utf-8")
            code, out, err = run_cli("impact", "--lock", f"child={split}", "--format", "text")
            self.assertEqual(code, 0, err)
            self.assertIn("roots=one:a.py, two:b.py cycle=no", out)


class ImpactMutationTests(unittest.TestCase):
    def test_forward_walk_is_killed(self):
        mutant = load_mutant(
            'children[copy["upstream"]].append(copy)',
            'children[copy["id"]].append(copy)',
        )
        locks = [
            _lock("origin", [_edge("lib.py", "upstream", "lib.py")]),
            _lock("leaf", [_edge("vendor/lib.py", "origin", "lib.py")]),
        ]
        real = topontology.impact_report(locks)
        changed = mutant.impact_report(locks)
        real_root = next(row for row in real["sources"] if row["id"] == "upstream:lib.py")
        self.assertEqual(real_root["downstream_repos"], 2)
        self.assertFalse(any(row["id"] == "upstream:lib.py" for row in changed["sources"]))

    def test_dropped_relay_check_is_killed(self):
        mutant = load_mutant(
            'if copy["repository"] != owner:',
            'if False and copy["repository"] != owner:',
        )
        locks = [
            _lock("origin", [_edge("lib.py", "upstream", "lib.py")]),
            _lock("leaf", [_edge("vendor/lib.py", "origin", "lib.py")]),
        ]
        real = topontology.impact_report(locks)
        changed = mutant.impact_report(locks)
        real_root = next(row for row in real["sources"] if row["id"] == "upstream:lib.py")
        changed_root = next(row for row in changed["sources"] if row["id"] == "upstream:lib.py")
        self.assertEqual(real_root["relays"], ["origin"])
        self.assertEqual(changed_root["relays"], [])
        self.assertEqual(changed_root["downstream_repos"], real_root["downstream_repos"])

    def test_overwriting_the_shortest_hop_is_killed(self):
        # child is reached directly and again through relay. A later write
        # replaces depth 1 with depth 2. The guarded assignment keeps 1.
        mutant = load_mutant(
            "if repo not in depth:\n                depth[repo] = hop",
            "depth[repo] = hop",
        )
        locks = [
            _lock("child", [
                _edge("direct.py", "upstream", "root.py"),
                _edge("via.py", "relay", "copy.py"),
            ]),
            _lock("relay", [_edge("copy.py", "upstream", "root.py")]),
        ]
        real = topontology.impact_report(locks)
        changed = mutant.impact_report(locks)
        real_child = next(item for item in real["sources"][0]["reached"] if item["repository"] == "child")
        changed_child = next(item for item in changed["sources"][0]["reached"] if item["repository"] == "child")
        self.assertEqual(real["sources"][0]["id"], "upstream:root.py")
        self.assertEqual(real_child["depth"], 1)
        self.assertEqual(changed_child["depth"], 2)

    def test_dropping_the_cycle_flag_is_killed_when_one_root_remains(self):
        # The cycle still reaches root:r.py, so len(roots) == 1. Without
        # on_cycle the copy is treated as a single canonical source.
        mutant = load_mutant(
            "if on_cycle or len(found) != 1:",
            "if len(found) != 1:",
        )
        locks = [
            _lock("alpha", [
                _edge("a.py", "beta", "b.py"),
                _edge("a.py", "root", "r.py"),
            ]),
            _lock("beta", [_edge("b.py", "alpha", "a.py")]),
        ]
        real = topontology.impact_report(locks)
        changed = mutant.impact_report(locks)
        self.assertFalse(real["policy"]["holds"])
        alpha = next(item for item in real["policy"]["violations"] if item["repository"] == "alpha")
        self.assertEqual(alpha["path"], "a.py")
        self.assertEqual(alpha["roots"], ["root:r.py"])
        self.assertTrue(alpha["cycle"])
        self.assertTrue(changed["policy"]["holds"])
        self.assertEqual(changed["policy"]["violations"], [])


if __name__ == "__main__":
    unittest.main()
