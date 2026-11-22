"""Declared SIBLINGS paths against imports parsed from local source trees."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_series import load_mutant
from test_topontology import ROOT, run_cli

FIXTURE = ROOT / "tests" / "fixtures" / "siblings"
DECLARED = FIXTURE / "aoi.py"
IMPORT_MAP = FIXTURE / "import-map.json"
DRAGOWING = FIXTURE / "dragowing"
QUERYU = FIXTURE / "queryu"
SOURCES = [
    {"repo": "dragowing", "path": DRAGOWING},
    {"repo": "queryu", "path": QUERYU},
]


def _targets(rows):
    return [row["target"] for row in rows]


def _blob(path):
    data = path.read_bytes()
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


class RealSiblingTests(unittest.TestCase):
    def test_fixture_bytes_match_the_recorded_commits(self):
        recorded = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
        for item in recorded["files"]:
            path = FIXTURE / item["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
            self.assertEqual(_blob(path), item["git_blob"])

    def test_real_aoi_siblings_keep_the_nagoyaction_assignment(self):
        table = topontology.siblings_from_source(DECLARED.read_text(encoding="utf-8"))
        self.assertEqual(list(table), [
            "blueprobe", "queryu", "sakanalytics", "pythdragoras", "dragowing", "nagoyaction",
        ])
        self.assertEqual(table["nagoyaction"], (
            "queryu", "queryu/ask", "sakanalytics/npb", "blueprobe", "blueprobe/npb", ".",
        ))
        self.assertEqual(table["dragowing"], ("pythdragoras", "pythdragoras/core", "queryu/ask"))
        self.assertEqual(table["queryu"], ("blueprobe", "blueprobe/npb"))
        edges = topontology.graph_from_siblings(table)["edges"]
        self.assertEqual(len(edges), 20)
        self.assertEqual(
            [edge["target"] for edge in edges if edge["source"] == "nagoyaction"],
            [".", "blueprobe", "blueprobe/npb", "queryu", "queryu/ask", "sakanalytics/npb"],
        )
        dot = next(edge for edge in edges if edge["target"] == ".")
        self.assertEqual(dot["basis"], "aoi")
        self.assertEqual(dot["relation"], "declares")

    def test_copied_files_match_the_hand_count(self):
        body, files = topontology.dependency_report(DECLARED, SOURCES, IMPORT_MAP)
        self.assertEqual(body["scanned"], ["dragowing", "queryu"])
        self.assertEqual(body["not_scanned"], ["blueprobe", "nagoyaction", "pythdragoras", "sakanalytics"])
        dragowing = body["repos"]["dragowing"]
        self.assertEqual(_targets(dragowing["matching"]), ["pythdragoras/core", "queryu/ask"])
        self.assertEqual(dragowing["matching"][0]["modules"], ["propositions"])
        self.assertEqual(dragowing["matching"][0]["files"], ["tests/test_forms.py"])
        self.assertEqual(dragowing["matching"][0]["repo"], "pythdragoras")
        self.assertEqual(dragowing["matching"][1]["modules"], ["ask"])
        self.assertEqual(dragowing["matching"][1]["repo"], "queryu")
        self.assertEqual(_targets(dragowing["declared_but_unused"]), ["pythdragoras"])
        self.assertEqual(dragowing["declared_but_unused"][0]["repo"], "pythdragoras")
        self.assertEqual(_targets(dragowing["used_but_undeclared"]), ["."])
        self.assertEqual(dragowing["used_but_undeclared"][0]["modules"], ["aoi", "aoi_paths"])
        self.assertEqual(dragowing["used_but_undeclared"][0]["files"], ["conftest.py", "tests/test_forms.py"])
        self.assertEqual(dragowing["used_but_undeclared"][0]["repo"], "aoi")
        self.assertIn({"module": "json", "path": "tests/test_forms.py", "line": 2}, dragowing["unmapped"])
        self.assertIn({"module": "dragowing.dragowing", "path": "tests/test_forms.py", "line": 10}, dragowing["unmapped"])
        self.assertIn({"module": "dragowing.dragowing", "path": "tests/test_forms.py", "line": 130}, dragowing["unmapped"])
        self.assertEqual(
            [(row["path"], row["line"], row["module"]) for row in dragowing["unmapped"]],
            sorted((row["path"], row["line"], row["module"]) for row in dragowing["unmapped"]),
        )
        queryu = body["repos"]["queryu"]
        self.assertEqual(_targets(queryu["matching"]), ["blueprobe"])
        self.assertEqual(queryu["matching"][0]["modules"], ["blueprobe"])
        self.assertEqual(queryu["matching"][0]["files"], ["conftest.py"])
        self.assertEqual(_targets(queryu["declared_but_unused"]), ["blueprobe/npb"])
        self.assertEqual(_targets(queryu["used_but_undeclared"]), ["queryu/ask"])
        self.assertEqual(queryu["used_but_undeclared"][0]["modules"], ["ask"])
        self.assertEqual(queryu["used_but_undeclared"][0]["repo"], "queryu")
        self.assertEqual(
            [(row["line"], row["module"]) for row in queryu["unmapped"]],
            [(6, "json"), (6, "os"), (6, "sys"), (7, "pathlib"), (9, "pytest")],
        )
        self.assertEqual([item["path"] for item in files], [
            "conftest.py", "tests/test_forms.py", "conftest.py",
        ])
        self.assertEqual(files[0]["sha256"], hashlib.sha256((DRAGOWING / "conftest.py").read_bytes()).hexdigest())

    def test_without_the_map_only_single_component_paths_match(self):
        body, _files = topontology.dependency_report(DECLARED, SOURCES, None)
        dragowing = body["repos"]["dragowing"]
        self.assertEqual(dragowing["matching"], [])
        self.assertEqual(_targets(dragowing["declared_but_unused"]), [
            "pythdragoras", "pythdragoras/core", "queryu/ask",
        ])
        modules = {row["module"] for row in dragowing["unmapped"]}
        self.assertIn("propositions", modules)
        self.assertIn("ask", modules)
        self.assertIn("aoi", modules)
        self.assertEqual(_targets(body["repos"]["queryu"]["matching"]), ["blueprobe"])
        self.assertEqual(body["repos"]["queryu"]["used_but_undeclared"], [])

    def test_declared_graph_counts_the_mutual_sibling_cycle(self):
        code, out, err = run_cli(
            "tests/fixtures/siblings/aoi.py",
            "tests/fixtures/sibling-classes.json",
            "siblings",
            "--graph-format", "aoi-siblings",
            "--format", "canonical",
        )
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        body = json.loads(out)
        expected = (ROOT / "tests/expected/siblings-declared.json").read_text(encoding="utf-8")
        self.assertEqual(out, expected)
        row = body["classes"]["declared"]
        self.assertEqual(row["edges"], 20)
        self.assertEqual(row["incident_nodes"], 12)
        self.assertEqual(row["weakly_connected_components"]["count"], 1)
        self.assertEqual(row["independent_cycles"], 9)
        self.assertEqual(row["order_cycle"], ["blueprobe", "queryu", "blueprobe"])
        self.assertIn(["pythdragoras", "pythdragoras"], row["sample_cycles"])
        self.assertEqual(body["classes"]["actual"]["edges"], 0)
        self.assertEqual(body["unclassified_edges"], 0)
        self.assertEqual(body["tool"]["version"], topontology.__version__)

    def test_py_imports_graph_keeps_unmapped_edges(self):
        code, out, err = run_cli(
            "tests/fixtures/siblings/py-imports.json",
            "tests/fixtures/sibling-classes.json",
            "siblings",
            "--graph-format", "py-imports",
            "--format", "canonical",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, (ROOT / "tests/expected/siblings-imports.json").read_text(encoding="utf-8"))
        body = json.loads(out)
        self.assertEqual(body["classes"]["actual"]["edges"], 7)
        self.assertEqual(body["classes"]["actual"]["multiple_sources"], ["queryu/ask"])
        self.assertEqual(body["classes"]["unmapped"]["edges"], 20)
        self.assertEqual(
            body["classes"]["unmapped"]["independent_cycles"],
            20 - 13 + 1,
        )


class MappingTests(unittest.TestCase):
    def test_parser_accepts_assign_list_and_later_subscript(self):
        text = (
            "other: int = 1\n"
            "SIBLINGS['skip']: tuple = ('nope',)\n"
            "x = 1\n"
            "a = SIBLINGS = {'ignored': ('no',)}\n"
            "item.attr['a'] = ('no',)\n"
            "other['a'] = ('no',)\n"
            "SIBLINGS = {'aa': ['b'], **{'c': ('d',)}}\n"
        )
        with self.assertRaises(ValueError):
            topontology.siblings_from_source(text)
        text = (
            "def ignored():\n"
            "    return SIBLINGS['aa']\n"
            "count = 0\n"
            "count += 1\n"
            "del count\n"
            "other = {}\n"
            "other.update({})\n"
            "other['a'].append('c')\n"
            "len(SIBLINGS)\n"
            "SIBLINGS.get('aa')\n"
            "SIBLINGS.keys()\n"
            "SIBLINGS = {'aa': ['b'], 'bb': ('c', 'd')}\n"
            "other['a'] = ('no',)\n"
            "item.attr['a'] = ('no',)\n"
            "SIBLINGS['cc'] = ('e',)\n"
            "SIBLINGS['aa'] = ('z',)\n"
            "if 'aa' in SIBLINGS:\n"
            "    y = SIBLINGS['aa']\n"
        )
        table = topontology.siblings_from_source(text)
        self.assertEqual(table, {"aa": ("z",), "bb": ("c", "d"), "cc": ("e",)})

    def test_unsupported_siblings_mutations_raise(self):
        base = "SIBLINGS = {'a': ('b',)}\n"
        forms = [
            "SIBLINGS['a'] += ('c',)\n",
            "SIBLINGS |= {'b': ('c',)}\n",
            "SIBLINGS.update({'b': ('c',)})\n",
            "SIBLINGS['a'].append('c')\n",
            "del SIBLINGS['a']\n",
            "del SIBLINGS\n",
            "del other, SIBLINGS['a']\n",
            "if True:\n    SIBLINGS['a'] = ('c',)\n",
            "if True:\n    SIBLINGS: dict = {'a': ('c',)}\n",
            "for _ in ():\n    SIBLINGS = {'a': ('c',)}\n",
            "def rewrite():\n    SIBLINGS['a'] = ('c',)\n",
            "if (SIBLINGS := {'a': ('c',)}):\n    pass\n",
            "if True:\n    a = SIBLINGS = {'a': ('c',)}\n",
            "SIBLINGS.attr += ('c',)\n",
        ]
        for form in forms:
            with self.assertRaises(ValueError) as raised:
                topontology.siblings_from_source(base + form)
            self.assertIn("unsupported SIBLINGS mutation", str(raised.exception))

    def test_parser_rejects_bad_shapes(self):
        samples = [
            "SIBLINGS: dict",
            "SIBLINGS: dict = {'a': 'b'}",
            "SIBLINGS = {'a': (1,)}",
            "SIBLINGS = {1: ('a',)}",
            "SIBLINGS = {'aa': ('b',), **{'c': ('d',)}}",
            "SIBLINGS['a'] = ('b',)",
            "SIBLINGS = {'a': ()}\nSIBLINGS[1] = ('b',)",
            "SIBLINGS = 1",
            "(",
            "x = 1",
        ]
        for text in samples:
            with self.assertRaises(ValueError):
                topontology.siblings_from_source(text)

    def test_graph_rejects_bad_paths_and_roots(self):
        for table in ({"": ("a",)}, {1: ("a",)}):
            with self.assertRaises(ValueError):
                topontology.graph_from_siblings(table)
        for path in ("", " a", "/a", "a/", "a/b "):
            with self.assertRaises(ValueError):
                topontology.graph_from_siblings({"child": (path,)})
        graph = topontology.graph_from_siblings({"child": (".", "a", "a/b")}, root=" launcher ")
        self.assertEqual([edge["basis"] for edge in graph["edges"]], ["launcher", "a", "a"])
        for root in ("", "  ", None):
            with self.assertRaises(ValueError):
                topontology.graph_from_siblings({"child": ("a",)}, root=root)
        self.assertEqual(topontology.graph_from_siblings({"child": ()})["edges"], [])

    def test_slashed_declared_paths_are_not_module_names(self):
        found = topontology.declared_module_paths({
            "child": ("pythdragoras/core", "pythdragoras", "."),
            "other": ("queryu/ask",),
        })
        self.assertEqual(found, {"pythdragoras": "pythdragoras"})
        self.assertNotIn("pythdragoras/core", found)
        self.assertNotIn("queryu/ask", found)
        self.assertNotIn(".", found)

    def test_import_map_rules_and_longest_prefix(self):
        parsed = topontology.load_import_map({
            "schema": "import-map/1",
            "modules": {"a": " path-a ", "a.b": "path-ab"},
        })
        self.assertEqual(parsed["root"], "aoi")
        self.assertEqual(parsed["modules"]["a"], "path-a")
        explicit = parsed["modules"]
        declared = {"blueprobe": "blueprobe"}
        self.assertEqual(topontology._mapped_path("a.b.c", explicit, declared), "path-ab")
        self.assertEqual(topontology._mapped_path("a.c", explicit, declared), "path-a")
        self.assertEqual(topontology._mapped_path("blueprobe.npb", explicit, declared), "blueprobe")
        self.assertIsNone(topontology._mapped_path("json", explicit, declared))
        self.assertIsNone(topontology._mapped_path(".blueprobe", explicit, declared))
        self.assertEqual(
            topontology.load_import_map({"schema": "import-map/1", "root": " launcher "})["root"],
            "launcher",
        )
        for document in (
            [],
            {"schema": "import-map/2"},
            {"schema": "import-map/1", "root": ""},
            {"schema": "import-map/1", "root": 1},
            {"schema": "import-map/1", "modules": []},
            {"schema": "import-map/1", "modules": {"": "a"}},
            {"schema": "import-map/1", "modules": {".a": "a"}},
            {"schema": "import-map/1", "modules": {1: "a"}},
            {"schema": "import-map/1", "modules": {"a": ""}},
            {"schema": "import-map/1", "modules": {"a": 1}},
        ):
            with self.assertRaises(ValueError):
                topontology.load_import_map(document)

    def test_scan_skips_cache_dirs_and_keeps_relative_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "mod.py").write_text(
                "import json as js\nfrom os import *\nfrom . import blueprobe\nfrom .blueprobe import x\n",
                encoding="utf-8",
            )
            (root / "build.py").write_text("import blueprobe\n", encoding="utf-8")
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "hidden.py").write_text("import sakanalytics\n", encoding="utf-8")
            (root / "bad.py").write_text("def (\n", encoding="utf-8")
            table = {"child": ("blueprobe",)}
            import_map = {"root": "aoi", "modules": {}}
            with self.assertRaises(ValueError) as raised:
                topontology.scan_imports([{"repo": "child", "path": root}], table, import_map)
            self.assertIn("bad.py", str(raised.exception))
            (root / "bad.py").unlink()
            graph = topontology.scan_imports([{"repo": "child", "path": root}], table, import_map)
            imported = [edge["target"] for edge in graph["edges"] if edge["relation"] == "imports"]
            unmapped = {(edge["target"], edge["basis"]) for edge in graph["edges"] if edge["relation"] == "unmapped"}
            self.assertEqual(imported, ["blueprobe"])
            self.assertIn(("json", "pkg/mod.py"), unmapped)
            self.assertIn(("os", "pkg/mod.py"), unmapped)
            self.assertIn((".blueprobe", "pkg/mod.py"), unmapped)
            self.assertNotIn(("sakanalytics", "__pycache__/hidden.py"), unmapped)
            self.assertEqual([item["path"] for item in graph["files"]], ["build.py", "pkg/mod.py"])
        with self.assertRaises(ValueError):
            topontology.python_sources(DECLARED)

    def test_symlink_outside_the_source_root_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "real.py").write_text("import kept\n", encoding="utf-8")
            (root / "link_inside.py").symlink_to(Path("real.py"))
            (outside / "secret.py").write_text("import SECRET\n", encoding="utf-8")
            (root / "link_file.py").symlink_to(Path("..") / "outside" / "secret.py")
            with self.assertRaises(ValueError) as raised:
                topontology.python_sources(root)
            self.assertIn("link_file.py", str(raised.exception))
            (root / "link_file.py").unlink()
            found = topontology.python_sources(root)
            self.assertEqual([rel for rel, _path in found], ["link_inside.py", "real.py"])
            graph = topontology.scan_imports(
                [{"repo": "child", "path": root}],
                {"child": ()},
                {"root": "aoi", "modules": {}},
            )
            modules = {edge["target"] for edge in graph["edges"]}
            self.assertIn("kept", modules)
            self.assertNotIn("SECRET", modules)

    def test_empty_directory_is_scanned_and_not_called_unused_for_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            body, files = topontology.dependency_report(
                DECLARED, [{"repo": "dragowing", "path": empty}], IMPORT_MAP,
            )
            self.assertEqual(files, [])
            self.assertEqual(body["repos"]["dragowing"]["matching"], [])
            self.assertEqual(len(body["repos"]["dragowing"]["declared_but_unused"]), 3)
            self.assertEqual(body["repos"]["dragowing"]["unmapped"], [])
            self.assertIn("queryu", body["not_scanned"])

    def test_manifest_accepts_absolute_paths_and_rejects_bad_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            base = work / "manifest"
            base.mkdir()
            declared = base / "declared.py"
            declared.write_text("SIBLINGS = {'q': ('blueprobe',)}\n", encoding="utf-8")
            (base / "src").mkdir()
            (base / "src" / "use.py").write_text("import blueprobe\n", encoding="utf-8")
            (base / "nested").mkdir()
            graph = topontology.graph_from_py_imports_document({
                "schema": "py-imports/1",
                "declared": str(declared.resolve()),
                "sources": [{"repo": " queryu ", "path": str((base / "src").resolve())}],
            }, base)
            targets = {edge["target"] for edge in graph["edges"] if edge["relation"] == "imports"}
            self.assertEqual(targets, {"blueprobe"})
            stayed = topontology._manifest_path(base, "nested/../declared.py", "declared")
            self.assertEqual(stayed.resolve(), declared.resolve())
            (base / "map.json").write_text(
                json.dumps({"schema": "import-map/1", "modules": {"ask": "queryu/ask"}}),
                encoding="utf-8",
            )
            (base / "src" / "use.py").write_text("import ask\n", encoding="utf-8")
            graph = topontology.graph_from_py_imports_document({
                "schema": "py-imports/1",
                "declared": "declared.py",
                "import_map": "map.json",
                "sources": [{"repo": "q", "path": "src"}],
            }, base)
            imported = [edge for edge in graph["edges"] if edge["relation"] == "imports"]
            self.assertEqual(imported[0]["target"], "queryu/ask")
            self.assertEqual(imported[0]["basis"], "ask")
            outside = work / "outside"
            outside.mkdir()
            (outside / "declared.py").write_text("SIBLINGS = {'escaped': ('secret',)}\n", encoding="utf-8")
            (outside / "src").mkdir()
            (base / "link.py").symlink_to(outside / "declared.py")
            for value in ("../outside/declared.py", str(outside / "declared.py"), "link.py"):
                with self.assertRaises(ValueError) as raised:
                    topontology._manifest_path(base, value, "declared")
                self.assertIn("escapes", str(raised.exception))
            with self.assertRaises(ValueError) as raised:
                topontology.graph_from_py_imports_document({
                    "schema": "py-imports/1",
                    "declared": "declared.py",
                    "sources": [{"repo": "q", "path": "../outside/src"}],
                }, base)
            self.assertIn("escapes", str(raised.exception))
        for document in (
            [],
            {"schema": "py-imports/0"},
            {"schema": "py-imports/1"},
            {"schema": "py-imports/1", "declared": "", "sources": [{"repo": "a", "path": "."}]},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": []},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": "no"},
            {"schema": "py-imports/1", "declared": "aoi.py", "import_map": "", "sources": [{"repo": "a", "path": "."}]},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": ["no"]},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": [{"repo": "", "path": "."}]},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": [{"repo": "a", "path": "."}, {"repo": "a", "path": "."}]},
            {"schema": "py-imports/1", "declared": "aoi.py", "sources": [{"repo": "a", "path": "missing-dir"}]},
        ):
            with self.assertRaises(ValueError):
                topontology.graph_from_py_imports_document(document, FIXTURE)
        with self.assertRaises(ValueError):
            topontology._load_graph(DECLARED, "no-such-format")


class DepsCliTests(unittest.TestCase):
    def _args(self, *extra):
        return [
            "deps",
            "--declared", "tests/fixtures/siblings/aoi.py",
            "--import-map", "tests/fixtures/siblings/import-map.json",
            "--source", "dragowing=tests/fixtures/siblings/dragowing",
            "--source", "queryu=tests/fixtures/siblings/queryu",
            *extra,
        ]

    def test_canonical_output_and_text(self):
        code, out, err = run_cli(*self._args("--format", "canonical"))
        self.assertEqual(code, 0, err)
        self.assertEqual(out, (ROOT / "tests/expected/siblings-deps.json").read_text(encoding="utf-8"))
        code, text, err = run_cli(*self._args("--format", "text"))
        self.assertEqual(code, 0, err)
        self.assertIn(f"tool: topontology {topontology.__version__}", text)
        self.assertIn("declared: tests/fixtures/siblings/aoi.py", text)
        self.assertIn("import_map:", text)
        self.assertIn("not_scanned: blueprobe, nagoyaction, pythdragoras, sakanalytics", text)
        self.assertIn("matching: pythdragoras/core -> pythdragoras modules=propositions files=tests/test_forms.py", text)
        self.assertIn("declared_but_unused: pythdragoras -> pythdragoras", text)
        self.assertIn("used_but_undeclared: . -> aoi modules=aoi,aoi_paths", text)
        self.assertIn("tests/test_forms.py:2:json", text)
        code, again, err = run_cli(*self._args("--json"))
        self.assertEqual(code, 0, err)
        self.assertEqual(again, out)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.json"
            code, _stdout, err = run_cli(*self._args("-o", str(dest), "--format", "canonical"))
            self.assertEqual(code, 0, err)
            self.assertEqual(dest.read_text(encoding="utf-8"), out)

    def test_source_order_does_not_change_the_document(self):
        code, forward, err = run_cli(*self._args("--format", "canonical"))
        self.assertEqual(code, 0, err)
        code, backward, err = run_cli(
            "deps",
            "--declared", "tests/fixtures/siblings/aoi.py",
            "--import-map", "tests/fixtures/siblings/import-map.json",
            "--source", "queryu=tests/fixtures/siblings/queryu",
            "--source", "dragowing=tests/fixtures/siblings/dragowing",
            "--source", "   ",
            "--format", "canonical",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(forward, backward)

    def test_args_override_env_and_blank_env_is_unset(self):
        code, out, err = run_cli(
            "deps", "--format", "json",
            env={
                "TOPONTOLOGY_DECLARED": str(DECLARED),
                "TOPONTOLOGY_IMPORT_MAP": str(IMPORT_MAP),
                "TOPONTOLOGY_SOURCES": f"dragowing={DRAGOWING},queryu={QUERYU}",
            },
        )
        self.assertEqual(code, 1)
        self.assertIn("pass the declared file", err)
        self.assertNotIn("TOPONTOLOGY_DECLARED", err)
        code, forced, err = run_cli(
            "deps", "--declared", str(DECLARED), "--format", "json",
            "--source", f"queryu={QUERYU}",
            env={
                "TOPONTOLOGY_DECLARED": "missing.py",
                "TOPONTOLOGY_IMPORT_MAP": "   ",
                "TOPONTOLOGY_SOURCES": "dragowing=missing",
            },
        )
        self.assertEqual(code, 0, err)
        body = json.loads(forced)
        self.assertNotIn("import_map", body["provenance"])
        self.assertEqual(_targets(body["repos"]["queryu"]["matching"]), ["blueprobe"])
        self.assertIn("ask", {row["module"] for row in body["repos"]["queryu"]["unmapped"]})
        code, text, err = run_cli("deps", "--declared", str(DECLARED), "--source", f"queryu={QUERYU}", "--format", "text")
        self.assertEqual(code, 0, err)
        self.assertNotIn("import_map:", text)

    def test_errors_stay_one_line(self):
        cases = [
            ["deps"],
            ["deps", "--declared", str(DECLARED)],
            ["deps", "--declared", str(DECLARED), "--source", "   "],
            ["deps", "--declared", str(DECLARED), "--source", "dragowing"],
            ["deps", "--declared", str(DECLARED), "--source", f"dragowing={DRAGOWING}", "--source", f"dragowing={QUERYU}"],
            ["deps", "--declared", str(DECLARED), "--source", f"dragowing={DECLARED}"],
            ["deps", "--declared", str(DECLARED), "--import-map", str(DECLARED), "--source", f"queryu={QUERYU}"],
            ["deps", "--declared", str(DECLARED), "--source", f"queryu={QUERYU}", "--json", "--format", "text"],
            ["tests/fixtures/siblings/aoi.py", "tests/fixtures/sibling-classes.json", "siblings"],
        ]
        for args in cases:
            code, _out, err = run_cli(*args)
            self.assertEqual(code, 1, args)
            self.assertEqual(err.count("\n"), 1, err)
            self.assertNotIn("Traceback", err)

    def test_env_sources_and_series_of_declared_graphs(self):
        code, out, err = run_cli(
            "deps", "--format", "text",
            "--declared", str(DECLARED),
            "--import-map", str(IMPORT_MAP),
            "--source", f"queryu={QUERYU}",
            "--source", f"dragowing={DRAGOWING}",
            env={"TOPONTOLOGY_DECLARED": "missing.py", "TOPONTOLOGY_SOURCES": "nope=missing"},
        )
        self.assertEqual(code, 0, err)
        self.assertIn("repo dragowing:", out)
        code, out, err = run_cli(
            "series", "tests/fixtures/sibling-classes.json", "siblings",
            "--snapshot", "one=tests/fixtures/siblings/aoi.py",
            "--snapshot", "two=tests/fixtures/siblings/aoi.py",
            "--graph-format", "aoi-siblings",
            "--format", "json",
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["pairs"][0]["classes"]["declared"]["delta"]["edges"], 0)

    def test_text_covers_empty_buckets(self):
        body = {
            "tool": {"name": "topontology", "version": topontology.__version__},
            "provenance": {
                "declared": {"path": "declared.py", "sha256": "aa"},
                "sources": [{"repo": "solo", "path": "solo", "files": []}],
            },
            "scanned": [],
            "not_scanned": [],
            "repos": {},
            "siblings_cover": {
                "complete": True,
                "cover": [],
                "method": "exact",
                "size": 0,
                "uncovered": [],
                "unique": {},
            },
        }
        text = topontology._deps_text(body)
        self.assertIn("scanned: (none)", text)
        self.assertIn("siblings_cover: exact size=0 (none)", text)
        self.assertIn("not_scanned: (none)", text)
        self.assertIn("repos: (none)", text)
        self.assertNotIn("import_map:", text)
        body["scanned"] = ["solo"]
        body["not_scanned"] = ["other"]
        body["repos"] = {"solo": {
            "matching": [{"target": "only-modules", "repo": "r", "modules": ["m"]}],
            "declared_but_unused": [],
            "used_but_undeclared": [{"target": "only-files", "repo": "r", "files": ["f.py"]}],
            "unmapped": [],
        }}
        text = topontology._deps_text(body)
        self.assertIn("matching: only-modules -> r modules=m", text)
        self.assertIn("used_but_undeclared: only-files -> r files=f.py", text)
        self.assertIn("declared_but_unused: (none)", text)
        self.assertIn("unmapped: (none)", text)
        self.assertEqual(topontology._edge_list([]), "(none)")
        self.assertEqual(topontology._unmapped_list([]), "(none)")


class MutationTests(unittest.TestCase):
    def test_intersection_swap_is_killed(self):
        mutant = load_mutant(
            "matching = sorted(declared_targets & imported_targets)",
            "matching = sorted(declared_targets - imported_targets)",
        )
        changed, _files = mutant.dependency_report(DECLARED, SOURCES, IMPORT_MAP)
        self.assertEqual(_targets(changed["repos"]["dragowing"]["matching"]), ["pythdragoras"])

    def test_dropping_unmapped_rows_is_killed(self):
        mutant = load_mutant(
            '"unmapped": _unmapped_rows(unmapped.get(repo, {})),',
            '"unmapped": [],',
        )
        changed, _files = mutant.dependency_report(DECLARED, SOURCES, IMPORT_MAP)
        self.assertEqual(changed["repos"]["dragowing"]["unmapped"], [])
        self.assertEqual(changed["repos"]["queryu"]["unmapped"], [])

    def test_ignoring_the_explicit_map_is_killed(self):
        mutant = load_mutant(
            "if key in explicit:\n            return explicit[key]",
            "if False and key in explicit:\n            return explicit[key]",
        )
        changed, _files = mutant.dependency_report(DECLARED, SOURCES, IMPORT_MAP)
        self.assertEqual(changed["repos"]["dragowing"]["matching"], [])
        self.assertEqual(_targets(changed["repos"]["queryu"]["matching"]), ["blueprobe"])

    def test_stripping_relative_dots_is_killed(self):
        mutant = load_mutant(
            'if module.startswith("."):\n        return None',
            'if module.startswith("."):\n        module = module.lstrip(".")',
        )
        self.assertEqual(mutant._mapped_path(".blueprobe", {}, {"blueprobe": "blueprobe"}), "blueprobe")
        self.assertIsNone(topontology._mapped_path(".blueprobe", {}, {"blueprobe": "blueprobe"}))

    def _original_table(self, text):
        return {"a": ("b",)}

    def test_ignoring_augmented_assignment_is_killed(self):
        mutant = load_mutant(
            "if isinstance(node, ast.AugAssign) and _names_siblings(node.target):",
            "if False and isinstance(node, ast.AugAssign) and _names_siblings(node.target):",
        )
        text = "SIBLINGS = {'a': ('b',)}\nSIBLINGS['a'] += ('c',)\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))
        with self.assertRaises(ValueError):
            topontology.siblings_from_source(text)

    def test_ignoring_delete_is_killed(self):
        mutant = load_mutant(
            "if isinstance(node, ast.Delete) and any(_names_siblings(target) for target in node.targets):",
            "if False and isinstance(node, ast.Delete) and any(_names_siblings(target) for target in node.targets):",
        )
        text = "SIBLINGS = {'a': ('b',)}\ndel SIBLINGS['a']\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))

    def test_ignoring_update_is_killed(self):
        mutant = load_mutant(
            '"update", "append", "extend", "insert", "pop", "remove", "clear",',
            '"append", "extend", "insert", "pop", "remove", "clear",',
        )
        text = "SIBLINGS = {'a': ('b',)}\nSIBLINGS.update({'b': ('c',)})\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))

    def test_ignoring_append_is_killed(self):
        mutant = load_mutant(
            '"update", "append", "extend", "insert", "pop", "remove", "clear",',
            '"update", "extend", "insert", "pop", "remove", "clear",',
        )
        text = "SIBLINGS = {'a': ('b',)}\nSIBLINGS['a'].append('c')\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))

    def test_ignoring_nested_assignment_is_killed(self):
        mutant = load_mutant(
            "if isinstance(node, ast.Assign) and any(_names_siblings(target) for target in node.targets):",
            "if False and isinstance(node, ast.Assign) and any(_names_siblings(target) for target in node.targets):",
        )
        text = "SIBLINGS = {'a': ('b',)}\nif True:\n    SIBLINGS['a'] = ('c',)\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))
        other = "SIBLINGS = {'a': ('b',)}\nfor _ in ():\n    SIBLINGS = {'a': ('c',)}\n"
        self.assertEqual(mutant.siblings_from_source(other), self._original_table(other))

    def test_ignoring_nested_annotation_is_killed(self):
        mutant = load_mutant(
            "if isinstance(node, ast.AnnAssign) and _names_siblings(node.target):",
            "if False and isinstance(node, ast.AnnAssign) and _names_siblings(node.target):",
        )
        text = "SIBLINGS = {'a': ('b',)}\nif True:\n    SIBLINGS: dict = {'a': ('c',)}\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))

    def test_ignoring_walrus_assignment_is_killed(self):
        mutant = load_mutant(
            "if isinstance(node, ast.NamedExpr) and _names_siblings(node.target):",
            "if False and isinstance(node, ast.NamedExpr) and _names_siblings(node.target):",
        )
        text = "SIBLINGS = {'a': ('b',)}\nif (SIBLINGS := {'a': ('c',)}):\n    pass\n"
        self.assertEqual(mutant.siblings_from_source(text), self._original_table(text))

    def test_symlink_escape_check_is_killed(self):
        mutant = load_mutant(
            "if path.is_symlink() and not path.resolve().is_relative_to(root):",
            "if False and path.is_symlink() and not path.resolve().is_relative_to(root):",
        )
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "root"
            outside = parent / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.py").write_text("import SECRET\n", encoding="utf-8")
            (root / "link_file.py").symlink_to(Path("..") / "outside" / "secret.py")
            found = [rel for rel, _path in mutant.python_sources(root)]
            self.assertEqual(found, ["link_file.py"])
            self.assertIn("SECRET", (root / "link_file.py").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                topontology.python_sources(root)

    def test_manifest_escape_check_is_killed(self):
        mutant = load_mutant(
            "if not resolved.is_relative_to(root):",
            "if False and not resolved.is_relative_to(root):",
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "manifest"
            outside = Path(tmp) / "outside"
            base.mkdir()
            outside.mkdir()
            target = outside / "declared.py"
            target.write_text("SIBLINGS = {'escaped': ('secret',)}\n", encoding="utf-8")
            escaped = mutant._manifest_path(base, "../outside/declared.py", "declared")
            self.assertEqual(escaped.resolve(), target.resolve())
            with self.assertRaises(ValueError):
                topontology._manifest_path(base, "../outside/declared.py", "declared")

    def test_dropping_the_slash_check_is_killed(self):
        mutant = load_mutant(
            'if "/" not in path and path != ".":',
            'if path != ".":',
        )
        table = {"child": ("pythdragoras/core", "pythdragoras", ".")}
        self.assertIn("pythdragoras/core", mutant.declared_module_paths(table))
        self.assertNotIn(".", mutant.declared_module_paths(table))
        self.assertNotIn("pythdragoras/core", topontology.declared_module_paths(table))

    def test_clearing_the_subscript_assignment_is_killed(self):
        mutant = load_mutant(
            "table[key.value] = _string_tuple(stmt.value)",
            "table[key.value] = ()",
        )
        table = mutant.siblings_from_source(DECLARED.read_text(encoding="utf-8"))
        self.assertEqual(table["nagoyaction"], ())
        self.assertEqual(
            topontology.siblings_from_source(DECLARED.read_text(encoding="utf-8"))["nagoyaction"][-1],
            ".",
        )


if __name__ == "__main__":
    unittest.main()
