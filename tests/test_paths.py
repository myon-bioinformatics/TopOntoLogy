"""Resolution order for ONTO and TaxOntOy, and the mutants that would break it."""
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import _clear_roots
import onto_path
import topontology
from test_topontology import ROOT, _traced_command, run_cli

MARKER = "knowledgegraph.py"
TABLE = "otl-relations.json"


@contextmanager
def _cleared_roots():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(onto_path.ONTO_ENV, None)
        os.environ.pop(onto_path.TAXONTOY_ENV, None)
        os.environ.pop(onto_path.OTL_ENV, None)
        yield


def _real_root(name):
    """The checkout this suite reads. A bad environment value falls back to inference."""
    try:
        return onto_path.resolve_root(name, anchor=ROOT)
    except onto_path.RootError:
        with _cleared_roots():
            return onto_path.resolve_root(name, anchor=ROOT)


REAL_ONTO = _real_root("ONTO")
REAL_TAX = _real_root("TaxOntOy")
REAL_OTL = _real_root("OnTautoLogy")


def load_path_mutant(old, new):
    """Execute a one-site edit of the real onto_path.py source."""
    source = Path(onto_path.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor count {source.count(old)} for {old!r}")
    spec = importlib.util.spec_from_loader("onto_path_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source.replace(old, new, 1), onto_path.__file__, "exec"), module.__dict__)
    return module


def _touch(path, text="marker\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class LayoutTests(unittest.TestCase):
    def test_real_siblings_resolve_from_a_file_a_directory_and_the_default_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "ONTO"
            tax = root / "TaxOntOy"
            _touch(onto / MARKER)
            _touch(tax / TABLE)
            project = root / "TopOntoLogy"
            script = _touch(project / "tool.py", "")
            # A checkout wired only by the environment must not win over this layout.
            with patch.dict(os.environ, {
                onto_path.ONTO_ENV: str(root / "other-onto"),
                onto_path.TAXONTOY_ENV: str(root / "other-tax"),
            }):
                with _cleared_roots():
                    self.assertEqual(onto_path.resolve_root("ONTO", anchor=script), onto.resolve())
                    self.assertEqual(onto_path.resolve_root("ONTO", anchor=project), onto.resolve())
                    self.assertEqual(onto_path.resolve_root("TaxOntOy", anchor=script), tax.resolve())
                    self.assertEqual(onto_path.resolve_root("TaxOntOy", anchor=project), tax.resolve())
                    self.assertEqual(
                        onto_path.marker_file("TaxOntOy", anchor=script),
                        (tax / TABLE).resolve(),
                    )
        self.assertEqual(onto_path._anchor_dir(None), Path(onto_path.__file__).resolve().parent)
        with self.assertRaises(onto_path.RootError) as raised:
            onto_path.resolve_root("missing-checkout", anchor=ROOT)
        self.assertIn("未知の checkout 名", str(raised.exception))
        self.assertEqual(topontology._profile_text("audit"), "audit")
        self.assertEqual(topontology._profile_text(Path("audit")), "audit")

    def test_explicit_beats_env_and_env_beats_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit"
            via_env = root / "from-env"
            sibling_parent = root / "workspace"
            project = sibling_parent / "TopOntoLogy"
            _touch(explicit / MARKER)
            _touch(via_env / MARKER)
            _touch(sibling_parent / "ONTO" / MARKER)
            anchor = _touch(project / "topontology.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                self.assertEqual(
                    onto_path.resolve_root("ONTO", explicit, anchor=anchor),
                    explicit.resolve(),
                )
                self.assertEqual(onto_path.resolve_root("ONTO", anchor=anchor), via_env.resolve())
            with _cleared_roots():
                self.assertEqual(
                    onto_path.resolve_root("ONTO", anchor=anchor),
                    (sibling_parent / "ONTO").resolve(),
                )

    def test_blank_explicit_does_not_fall_through_and_blank_env_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            via_env = root / "from-env"
            parent = root / "workspace"
            _touch(via_env / MARKER)
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TopOntoLogy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", "   ", anchor=anchor)
                self.assertIn("ONTO_ROOT", str(raised.exception))
                self.assertNotIn(str(via_env), str(raised.exception))
            with patch.dict(os.environ, {onto_path.ONTO_ENV: "  \t "}):
                self.assertEqual(
                    onto_path.resolve_root("ONTO", anchor=anchor),
                    (parent / "ONTO").resolve(),
                )

    def test_bad_env_does_not_infer_and_names_the_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "not-onto"
            bad.mkdir()
            parent = root / "workspace"
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TopOntoLogy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(bad)}):
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", anchor=anchor)
            message = str(raised.exception)
            self.assertIn("ONTO_ROOT", message)
            self.assertIn(str(bad.resolve()), message)
            self.assertIn(MARKER, message)

    def test_missing_checkout_names_each_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "alone" / "tool.py", "")
            with _cleared_roots():
                with self.assertRaises(onto_path.RootError) as onto_error:
                    onto_path.resolve_root("ONTO", anchor=anchor)
                with self.assertRaises(onto_path.RootError) as tax_error:
                    onto_path.resolve_root("TaxOntOy", anchor=anchor)
            self.assertIn("ONTO_ROOT= を渡す", str(onto_error.exception))
            self.assertIn("TAXONTOY_ROOT= を渡す", str(tax_error.exception))
            self.assertIn(MARKER, str(onto_error.exception))
            self.assertIn(TABLE, str(tax_error.exception))

    def test_child_checkout_and_grandchild_embedding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # CI layout: the checkout is inside the project, not beside it.
            project = root / "project"
            _touch(project / "ONTO" / MARKER, "child\n")
            _touch(project / "TaxOntOy" / TABLE, "{}\n")
            anchor = _touch(project / "topontology.py", "")
            with _cleared_roots():
                self.assertEqual(onto_path.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())
                self.assertEqual(
                    onto_path.resolve_root("TaxOntOy", anchor=anchor),
                    (project / "TaxOntOy").resolve(),
                )
            # Grandchild: markers live on an ancestor, not beside this directory.
            nested = root / "parent" / "vendor" / "TopOntoLogy"
            _touch(root / "parent" / "ONTO" / MARKER, "ancestor\n")
            _touch(root / "parent" / "TaxOntOy" / TABLE, "{}\n")
            deep = _touch(nested / "topontology.py", "")
            with _cleared_roots():
                self.assertEqual(
                    onto_path.infer_checkout("ONTO", MARKER, deep),
                    (root / "parent" / "ONTO").resolve(),
                )
                self.assertEqual(
                    onto_path.resolve_root("TaxOntOy", anchor=nested),
                    (root / "parent" / "TaxOntOy").resolve(),
                )

    def test_sibling_beats_a_nested_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TopOntoLogy"
            _touch(root / "ONTO" / MARKER, "sibling\n")
            _touch(project / "ONTO" / MARKER, "child\n")
            anchor = _touch(project / "topontology.py", "")
            with _cleared_roots():
                self.assertEqual(onto_path.resolve_root("ONTO", anchor=anchor), (root / "ONTO").resolve())

    def test_a_directory_marker_is_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decoy = root / "ONTO" / MARKER
            decoy.mkdir(parents=True)
            anchor = _touch(root / "TopOntoLogy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                with self.assertRaises(onto_path.RootError) as raised:
                    onto_path.resolve_root("ONTO", anchor=anchor)
            self.assertIn("ONTO_ROOT", str(raised.exception))

    def test_tilde_in_an_explicit_root_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "empty" / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                self.assertEqual(onto_path.resolve_root("ONTO", "~/ONTO", anchor=anchor), onto.resolve())


class OriginTests(unittest.TestCase):
    def test_install_is_first_and_idempotent_and_origin_must_match(self):
        entry = str(REAL_ONTO)
        saved_path = list(sys.path)
        saved_module = sys.modules.get("knowledgegraph")
        try:
            while entry in sys.path:
                sys.path.remove(entry)
            onto_path.install_root(REAL_ONTO)
            self.assertEqual(sys.path[0], entry)
            onto_path.install_root(REAL_ONTO)
            self.assertEqual(sys.path.count(entry), 1)
            self.assertEqual(onto_path.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV), REAL_ONTO)
            sys.modules.pop("knowledgegraph", None)
            self.assertEqual(onto_path.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV), REAL_ONTO)
            missing = types.ModuleType("knowledgegraph")
            missing.__file__ = None
            sys.modules["knowledgegraph"] = missing
            with self.assertRaises(onto_path.RootError) as raised:
                onto_path.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
            self.assertIn("(なし)", str(raised.exception))
            self.assertIn("ONTO_ROOT", str(raised.exception))
            wrong = types.ModuleType("knowledgegraph")
            wrong.__file__ = str(Path("/tmp") / MARKER)
            sys.modules["knowledgegraph"] = wrong
            with self.assertRaises(onto_path.RootError) as raised:
                onto_path.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
            self.assertIn(str(Path("/tmp") / MARKER), str(raised.exception))
        finally:
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module

    def test_configure_records_roots_without_clobbering_env(self):
        with patch.dict(os.environ, {onto_path.ONTO_ENV: "keep-onto", onto_path.TAXONTOY_ENV: "keep-tax"}):
            found = onto_path.bind(ROOT, {"ONTO": REAL_ONTO, "TaxOntOy": REAL_TAX})
            self.assertEqual(found["ONTO"], REAL_ONTO)
            self.assertEqual(found["TaxOntOy"], REAL_TAX)
            self.assertEqual(os.environ[onto_path.ONTO_ENV], "keep-onto")
            self.assertEqual(os.environ[onto_path.TAXONTOY_ENV], "keep-tax")
            self.assertEqual(onto_path.RESOLVED["ONTO"], REAL_ONTO)
            self.assertEqual(onto_path.RESOLVED["TaxOntOy"], REAL_TAX)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onto = root / "ONTO"
            tax = root / "TaxOntOy"
            otl = root / "OnTautoLogy"
            _touch(onto / MARKER, "marker = True\n")
            _touch(tax / TABLE, "{}\n")
            _touch(otl / onto_path.OTL_MARKER, "")
            anchor = _touch(root / "app" / "tool.py", "")
            saved_module = sys.modules.get("knowledgegraph")
            saved_otl = sys.modules.get("ontautology_graphs")
            saved_path = list(sys.path)
            sys.modules.pop("knowledgegraph", None)
            sys.modules.pop("ontautology_graphs", None)
            try:
                with _cleared_roots():
                    found = onto_path.bind(anchor)
                    self.assertEqual(found["ONTO"], onto.resolve())
                    self.assertEqual(found["TaxOntOy"], tax.resolve())
                    self.assertEqual(found["OnTautoLogy"], otl.resolve())
                    self.assertEqual(os.environ[onto_path.ONTO_ENV], str(onto.resolve()))
                    self.assertEqual(os.environ[onto_path.TAXONTOY_ENV], str(tax.resolve()))
                    self.assertEqual(os.environ[onto_path.OTL_ENV], str(otl.resolve()))
            finally:
                sys.path[:] = saved_path
                if saved_module is None:
                    sys.modules.pop("knowledgegraph", None)
                else:
                    sys.modules["knowledgegraph"] = saved_module
                if saved_otl is None:
                    sys.modules.pop("ontautology_graphs", None)
                else:
                    sys.modules["ontautology_graphs"] = saved_otl


class ArgvTests(unittest.TestCase):
    def test_flags_forms_stop_and_missing_values(self):
        blank = {"ONTO": None, "TaxOntOy": None, "OnTautoLogy": None}
        self.assertEqual(onto_path.explicit_from_argv([]), blank)
        self.assertEqual(
            onto_path.explicit_from_argv(["graph", "--onto-root", "/onto", "--taxontoy-root", "/tax"]),
            {"ONTO": "/onto", "TaxOntOy": "/tax", "OnTautoLogy": None},
        )
        self.assertEqual(
            onto_path.explicit_from_argv(["--onto-root=/onto", "--taxontoy-root=/tax", "audit"]),
            {"ONTO": "/onto", "TaxOntOy": "/tax", "OnTautoLogy": None},
        )
        self.assertEqual(
            onto_path.explicit_from_argv(["--onto-root", "/first", "--onto-root", "/second"]),
            {"ONTO": "/second", "TaxOntOy": None, "OnTautoLogy": None},
        )
        self.assertEqual(
            onto_path.explicit_from_argv(["--", "--onto-root", "/ignored"]),
            blank,
        )
        self.assertEqual(onto_path.explicit_from_argv(["--onto-root-extra", "/nope"]), blank)
        self.assertEqual(onto_path.explicit_from_argv(["--onto-root="]), {"ONTO": "", "TaxOntOy": None, "OnTautoLogy": None})
        with self.assertRaises(onto_path.RootError) as raised:
            onto_path.explicit_from_argv(["--onto-root"])
        self.assertIn("ONTO_ROOT", str(raised.exception))
        with self.assertRaises(onto_path.RootError) as raised:
            onto_path.explicit_from_argv(["--taxontoy-root"])
        self.assertIn("TAXONTOY_ROOT", str(raised.exception))


class CliResolutionTests(unittest.TestCase):
    def test_short_form_infers_the_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "app"
            project.mkdir()
            onto = root / "ONTO"
            tax = root / "TaxOntOy"
            onto.mkdir()
            tax.mkdir()
            shutil.copy(REAL_ONTO / MARKER, onto / MARKER)
            table = tax / TABLE
            shutil.copy(REAL_TAX / TABLE, table)
            otl = root / "OnTautoLogy"
            otl.mkdir()
            shutil.copy(REAL_OTL / onto_path.OTL_MARKER, otl / onto_path.OTL_MARKER)
            shutil.copy(ROOT / "onto_path.py", project / "onto_path.py")
            shutil.copy(ROOT / "topontology.py", project / "topontology.py")
            graph = ROOT / "tests/fixtures/graph.json"
            code, out, err = _run_script(
                project / "topontology.py",
                [str(graph), "concepts"],
                {"ONTO_ROOT": None, "TAXONTOY_ROOT": None, "OTL_ROOT": None, "PYTHONPATH": None},
            )
            self.assertEqual(code, 0, err)
            body = json.loads(out)
            self.assertTrue(body["provenance"]["classification"]["path"].endswith("TaxOntOy/otl-relations.json"))
            self.assertEqual(
                body["provenance"]["classification"]["sha256"],
                hashlib.sha256(table.read_bytes()).hexdigest(),
            )
            self.assertEqual(body["provenance"]["profile"], "concepts")
            self.assertEqual(body["classes"]["hierarchy"]["independent_cycles"], 1)
            self.assertEqual(body["unclassified_edges"], 1)

    def test_option_beats_env_and_env_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad"
            bad.mkdir()
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts",
                "--onto-root", str(bad),
                env={onto_path.ONTO_ENV: str(REAL_ONTO), onto_path.TAXONTOY_ENV: str(REAL_TAX)},
            )
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("ONTO_ROOT", err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts",
                env={onto_path.ONTO_ENV: str(bad), onto_path.TAXONTOY_ENV: str(REAL_TAX), "PYTHONPATH": None},
            )
            self.assertEqual(code, 1)
            self.assertIn("ONTO_ROOT", err)
            self.assertIn(str(bad.resolve()), err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts",
                "--onto-root", str(REAL_ONTO),
                "--taxontoy-root", str(REAL_TAX),
                env={onto_path.ONTO_ENV: str(bad), onto_path.TAXONTOY_ENV: str(bad)},
            )
            self.assertEqual(code, 0, err)
            code, out, err = run_cli("--onto-root")
            self.assertEqual(code, 1)
            self.assertIn("ONTO_ROOT", err)
            self.assertNotIn("Traceback", err)

    def test_a_wrong_root_env_does_not_block_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = str(Path(tmp) / "bad")
            Path(bad).mkdir()
            env = os.environ.copy()
            env.pop("COVERAGE_PROCESS_START", None)
            env.update({
                onto_path.ONTO_ENV: bad,
                onto_path.TAXONTOY_ENV: bad,
                onto_path.OTL_ENV: bad,
            })
            completed = subprocess.run(
                [sys.executable, "-S", "-c",
                 "import os, sys\n"
                 "os.environ['ONTO_ROOT'] = sys.argv[1]\n"
                 "os.environ['TAXONTOY_ROOT'] = sys.argv[1]\n"
                 "os.environ['OTL_ROOT'] = sys.argv[1]\n"
                 "sys.path.insert(0, sys.argv[2])\n"
                 "import _clear_roots\n"
                 "import topontology\n"
                 "print(topontology.__version__)\n",
                 bad, str(ROOT / "tests")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(topontology.__version__, completed.stdout)
        self.assertNotIn("Traceback", completed.stderr)

    def test_classification_option_beats_the_middle_positional(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v3.json", "demo",
            "--classification", "tests/fixtures/classes.json",
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertTrue(body["provenance"]["classification"]["path"].endswith("classes.json"))
        self.assertEqual(body["provenance"]["profile"], "demo")

    def test_pythonpath_cannot_hide_the_resolved_onto(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "fake"
            fake.mkdir()
            (fake / MARKER).write_text(
                "def graph_mermaid(*_a, **_k):\n"
                "    raise RuntimeError('WRONG_ONTO')\n"
                "def relation_cycles(*_a, **_k):\n"
                "    raise RuntimeError('WRONG_ONTO')\n"
                "def topological_order(*_a, **_k):\n"
                "    raise RuntimeError('WRONG_ONTO')\n",
                encoding="utf-8",
            )
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo",
                env={"PYTHONPATH": str(fake), onto_path.ONTO_ENV: str(REAL_ONTO)},
            )
            self.assertEqual(code, 0, err)
            self.assertNotIn("WRONG_ONTO", err)
            self.assertNotIn("WRONG_ONTO", out)

    def test_library_boot_reraises_a_bad_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {onto_path.ONTO_ENV: tmp}):
                with self.assertRaises(onto_path.RootError) as raised:
                    topontology._boot()
            self.assertIn("ONTO_ROOT", str(raised.exception))
            self.assertIn(str(Path(tmp).resolve()), str(raised.exception))

    def test_commands_with_an_explicit_table_do_not_need_taxontoy(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "not-a-checkout"
            bad.mkdir()
            env = {
                onto_path.ONTO_ENV: None,
                onto_path.TAXONTOY_ENV: str(bad),
                "PYTHONPATH": None,
            }
            onto = ("--onto-root", str(REAL_ONTO))
            cases = (
                ("--help", *onto),
                ("impact", "--help", *onto),
                ("deps", "--help", *onto),
                ("tests/fixtures/graph.json", "tests/fixtures/classes.json", "demo", *onto),
                (
                    "tests/fixtures/graph.json", "demo",
                    "--classification", "tests/fixtures/classes.json",
                    *onto,
                ),
                (
                    "impact",
                    "--lock", "child=tests/fixtures/aoi-locks/blueprobe.json",
                    *onto,
                ),
                (
                    "deps",
                    "--declared", "tests/fixtures/siblings/aoi.py",
                    "--source", "queryu=tests/fixtures/siblings/queryu",
                    *onto,
                ),
            )
            for args in cases:
                code, out, err = run_cli(*args, env=env)
                self.assertEqual(code, 0, (args, err))
                self.assertNotIn("TAXONTOY_ROOT", err)
                self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts", *onto, env=env,
            )
            self.assertEqual(code, 1)
            self.assertIn("TAXONTOY_ROOT", err)
            self.assertNotIn("Traceback", err)

    def test_empty_knowledgegraph_is_one_line_on_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "onto"
            empty.mkdir()
            (empty / MARKER).write_text("", encoding="utf-8")
            code, out, err = run_cli(
                "--help",
                "--onto-root", str(empty),
                env={onto_path.ONTO_ENV: None, onto_path.TAXONTOY_ENV: None, "PYTHONPATH": None},
            )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertNotIn("Traceback", err)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("knowledgegraph", err)

    def test_import_error_reraises_off_the_cli(self):
        saved_module = sys.modules.get("knowledgegraph")
        saved_path = list(sys.path)
        sys.modules.pop("knowledgegraph", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / MARKER).write_text("", encoding="utf-8")
                sys.path.insert(0, tmp)
                with self.assertRaises(ImportError):
                    topontology._load_graph_api()
        finally:
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module


class ConftestTests(unittest.TestCase):
    def test_pytest_does_not_collect_sibling_checkouts(self):
        tree = ast.parse((ROOT / "conftest.py").read_text(encoding="utf-8"))
        found = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "collect_ignore":
                        found = ast.literal_eval(node.value)
        self.assertEqual(found, ["ONTO", "TaxOntOy", "OnTautoLogy", "otl", "build"])

    def test_usage_error_names_the_env_var_and_checks_origin(self):
        stub = types.ModuleType("pytest")

        class UsageError(Exception):
            pass

        stub.UsageError = UsageError
        saved_pytest = sys.modules.get("pytest")
        saved_module = sys.modules.get("knowledgegraph")
        saved_otl = sys.modules.get("ontautology_graphs")
        saved_path = list(sys.path)
        sys.modules["pytest"] = stub
        try:
            spec = importlib.util.spec_from_file_location("tol_conftest_under_test", ROOT / "conftest.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with _cleared_roots():
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    missing = root / "nowhere"
                    missing.mkdir()
                    with self.assertRaises(UsageError) as raised:
                        module.activate(missing / "missing.py")
                    self.assertIn("ONTO_ROOT", str(raised.exception))
                    onto = root / "ONTO"
                    tax = root / "TaxOntOy"
                    otl = root / "OnTautoLogy"
                    _touch(onto / MARKER, "marker = True\n")
                    _touch(tax / TABLE, "{}\n")
                    _touch(otl / onto_path.OTL_MARKER, "")
                    anchor = _touch(root / "app" / "tool.py", "")
                    sys.modules.pop("knowledgegraph", None)
                    sys.modules.pop("ontautology_graphs", None)
                    found = module.activate(anchor)
                    self.assertEqual(found["ONTO"], onto.resolve())
                    self.assertEqual(found["TaxOntOy"], tax.resolve())
                    self.assertEqual(found["OnTautoLogy"], otl.resolve())
                    fake = types.ModuleType("knowledgegraph")
                    fake.__file__ = str(root / "other" / MARKER)
                    sys.modules["knowledgegraph"] = fake
                    with self.assertRaises(UsageError) as raised:
                        module.activate(anchor)
                    self.assertIn("出どころ", str(raised.exception))
                    self.assertIn("ONTO_ROOT", str(raised.exception))
        finally:
            if saved_pytest is None:
                sys.modules.pop("pytest", None)
            else:
                sys.modules["pytest"] = saved_pytest
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module
            if saved_otl is None:
                sys.modules.pop("ontautology_graphs", None)
            else:
                sys.modules["ontautology_graphs"] = saved_otl


def _run_script(script, args, env):
    """Run a copied script. Do not measure it; the copy is outside the repo."""
    merged = os.environ.copy()
    merged.pop("COVERAGE_PROCESS_START", None)
    for key, value in env.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    completed = subprocess.run(
        _traced_command(script, args, merged),
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


class ResolutionMutationTests(unittest.TestCase):
    def test_swapped_explicit_and_env_is_killed(self):
        mutant = load_path_mutant(
            "    chosen = _explicit_text(explicit)\n"
            "    if chosen is not None:\n"
            "        if chosen == \"\":\n"
            "            raise RootError(_missing(label, env_name, marker))\n"
            "        return _require_marker(chosen, marker, env_name, label)\n"
            "    chosen = _env_text(env_name)\n",
            "    chosen = _env_text(env_name)\n"
            "    if chosen is not None:\n"
            "        if chosen == \"\":\n"
            "            raise RootError(_missing(label, env_name, marker))\n"
            "        return _require_marker(chosen, marker, env_name, label)\n"
            "    chosen = _explicit_text(explicit)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "explicit"
            via_env = root / "env"
            _touch(explicit / MARKER, "explicit\n")
            _touch(via_env / MARKER, "env\n")
            anchor = _touch(root / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                self.assertEqual(onto_path.resolve_root("ONTO", explicit, anchor=anchor), explicit.resolve())
                self.assertEqual(mutant.resolve_root("ONTO", explicit, anchor=anchor), via_env.resolve())

    def test_blank_explicit_falling_through_is_killed(self):
        mutant = load_path_mutant(
            "    if not text:\n        return \"\"\n",
            "    if not text:\n        return None\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            via_env = Path(tmp) / "env"
            _touch(via_env / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(via_env)}):
                with self.assertRaises(onto_path.RootError):
                    onto_path.resolve_root("ONTO", " ", anchor=anchor)
                self.assertEqual(mutant.resolve_root("ONTO", " ", anchor=anchor), via_env.resolve())

    def test_skipping_the_marker_check_is_killed(self):
        mutant = load_path_mutant(
            "    if not (path / marker).is_file():\n",
            "    if False and not (path / marker).is_file():\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "empty"
            bad.mkdir()
            with self.assertRaises(onto_path.RootError):
                onto_path.resolve_root("ONTO", bad, anchor=Path(tmp))
            self.assertEqual(mutant.resolve_root("ONTO", bad, anchor=Path(tmp)), bad.resolve())

    def test_dropping_the_env_name_from_the_message_is_killed(self):
        mutant = load_path_mutant(
            '    return f"{label} が見つからない。{env_name}= を渡す（{marker} がある checkout）"\n',
            '    return f"{label} が見つからない。checkout を渡す（{marker}）"\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                with self.assertRaises(onto_path.RootError) as real:
                    onto_path.resolve_root("ONTO", anchor=anchor)
                with self.assertRaises(mutant.RootError) as changed:
                    mutant.resolve_root("ONTO", anchor=anchor)
        self.assertIn("ONTO_ROOT=", str(real.exception))
        self.assertNotIn("ONTO_ROOT", str(changed.exception))

    def test_skipping_the_origin_check_is_killed(self):
        mutant = load_path_mutant(
            "    root = Path(root).resolve()\n    problems = []\n",
            "    return Path(root).resolve()\n    problems = []\n",
        )
        saved = sys.modules.get("knowledgegraph")
        try:
            wrong = types.ModuleType("knowledgegraph")
            wrong.__file__ = "/tmp/knowledgegraph.py"
            sys.modules["knowledgegraph"] = wrong
            with self.assertRaises(onto_path.RootError):
                onto_path.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV)
            self.assertEqual(mutant.verify_origin(REAL_ONTO, onto_path.ONTO_MODULES, onto_path.ONTO_ENV), REAL_ONTO)
        finally:
            if saved is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved

    def test_not_walking_ancestors_is_killed(self):
        mutant = load_path_mutant(
            "    for candidate in (here, *here.parents):\n",
            "    for candidate in (here,):\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "parent" / "vendor" / "TopOntoLogy"
            _touch(root / "parent" / "ONTO" / MARKER)
            anchor = _touch(nested / "tool.py", "")
            self.assertEqual(
                onto_path.infer_checkout("ONTO", MARKER, anchor),
                (root / "parent" / "ONTO").resolve(),
            )
            self.assertIsNone(mutant.infer_checkout("ONTO", MARKER, anchor))

    def test_preferring_the_nested_copy_is_killed(self):
        mutant = load_path_mutant(
            "    found = _dir_if_marked(here.parent / dirname, marker)\n"
            "    if found is not None:\n"
            "        return found\n",
            "    found = None\n"
            "    if found is not None:\n"
            "        return found\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "TopOntoLogy"
            _touch(root / "ONTO" / MARKER, "sibling\n")
            _touch(project / "ONTO" / MARKER, "child\n")
            anchor = _touch(project / "tool.py", "")
            self.assertEqual(onto_path.infer_checkout("ONTO", MARKER, anchor), (root / "ONTO").resolve())
            self.assertEqual(mutant.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())

    def test_invalid_env_falling_through_to_inference_is_killed(self):
        mutant = load_path_mutant(
            "    chosen = _env_text(env_name)\n"
            "    if chosen is not None:\n"
            "        return _require_marker(chosen, marker, env_name, label)\n",
            "    chosen = _env_text(env_name)\n"
            "    if False and chosen is not None:\n"
            "        return _require_marker(chosen, marker, env_name, label)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "bad"
            bad.mkdir()
            parent = root / "workspace"
            _touch(parent / "ONTO" / MARKER)
            anchor = _touch(parent / "TopOntoLogy" / "tool.py", "")
            with patch.dict(os.environ, {onto_path.ONTO_ENV: str(bad)}):
                with self.assertRaises(onto_path.RootError):
                    onto_path.resolve_root("ONTO", anchor=anchor)
                self.assertEqual(
                    mutant.resolve_root("ONTO", anchor=anchor),
                    (parent / "ONTO").resolve(),
                )

    def test_appending_onto_to_sys_path_is_killed(self):
        mutant = load_path_mutant(
            "    sys.path.insert(0, entry)\n",
            "    sys.path.append(entry)\n",
        )
        saved = list(sys.path)
        try:
            entry = str(REAL_ONTO)
            while entry in sys.path:
                sys.path.remove(entry)
            decoy = str(Path("/tmp") / "decoy-onto")
            sys.path.insert(0, decoy)
            placed = mutant.install_root(REAL_ONTO)
            self.assertEqual(placed, entry)
            self.assertLess(sys.path.index(decoy), sys.path.index(entry))
            while entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, decoy)
            self.assertEqual(onto_path.install_root(REAL_ONTO), entry)
            self.assertEqual(sys.path[0], entry)
        finally:
            sys.path[:] = saved

    def test_overwriting_a_preset_root_env_is_killed(self):
        mutant = load_path_mutant(
            '        os.environ.setdefault(spec["env"], str(root))\n',
            '        os.environ[spec["env"]] = str(root)\n',
        )
        with patch.dict(os.environ, {onto_path.ONTO_ENV: "keep-onto", onto_path.TAXONTOY_ENV: str(REAL_TAX)}):
            onto_path.bind(ROOT, {"ONTO": REAL_ONTO, "TaxOntOy": REAL_TAX})
            self.assertEqual(os.environ[onto_path.ONTO_ENV], "keep-onto")
            mutant.bind(ROOT, {"ONTO": REAL_ONTO, "TaxOntOy": REAL_TAX})
            self.assertEqual(os.environ[onto_path.ONTO_ENV], str(REAL_ONTO))

    def test_ignoring_the_equals_form_is_killed(self):
        mutant = load_path_mutant(
            "            if token.startswith(prefix):\n",
            "            if False and token.startswith(prefix):\n",
        )
        argv = ["--onto-root=/onto", "--taxontoy-root=/tax"]
        self.assertEqual(onto_path.explicit_from_argv(argv), {"ONTO": "/onto", "TaxOntOy": "/tax", "OnTautoLogy": None})
        self.assertEqual(mutant.explicit_from_argv(argv), {"ONTO": None, "TaxOntOy": None, "OnTautoLogy": None})

    def test_skipping_tilde_expansion_is_killed(self):
        mutant = load_path_mutant(
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).expanduser().resolve()\n",
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).resolve()\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), onto_path.ONTO_ENV: ""}):
                os.environ.pop(onto_path.ONTO_ENV, None)
                self.assertEqual(onto_path.resolve_root("ONTO", "~/ONTO", anchor=anchor), onto.resolve())
                with self.assertRaises(mutant.RootError):
                    mutant.resolve_root("ONTO", "~/ONTO", anchor=anchor)


if __name__ == "__main__":
    unittest.main()
