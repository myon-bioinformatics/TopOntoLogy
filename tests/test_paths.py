"""Resolution order for ONTO and TaxOntOy, and the mutants that would break it."""
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import tol_paths
import topontology
from test_topontology import ROOT, run_cli

REAL_ONTO = tol_paths.resolve_onto(anchor=ROOT)
REAL_TAX = tol_paths.resolve_taxontoy(anchor=ROOT)
MARKER = "knowledgegraph.py"
TABLE = "otl-relations.json"


@contextmanager
def _cleared_roots():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop(tol_paths.ONTO_ENV, None)
        os.environ.pop(tol_paths.TAXONTOY_ENV, None)
        yield


def load_tol_mutant(old, new):
    """Execute a one-site edit of the real tol_paths.py source."""
    source = Path(tol_paths.__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError(f"mutant anchor count {source.count(old)} for {old!r}")
    spec = importlib.util.spec_from_loader("tol_paths_mutant", loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source.replace(old, new, 1), tol_paths.__file__, "exec"), module.__dict__)
    return module


def _touch(path, text="marker\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class LayoutTests(unittest.TestCase):
    def test_real_siblings_resolve_from_a_file_a_directory_and_the_default_anchor(self):
        for anchor in (ROOT / "tol_paths.py", ROOT, None):
            self.assertEqual(tol_paths.resolve_onto(anchor=anchor), REAL_ONTO)
            self.assertEqual(tol_paths.resolve_taxontoy(anchor=anchor), REAL_TAX)
        self.assertEqual(tol_paths._anchor_dir(None), Path(tol_paths.__file__).resolve().parent)
        self.assertEqual(topontology._profile_text("audit"), "audit")
        self.assertEqual(topontology._profile_text(Path("audit")), "audit")
        self.assertEqual(
            tol_paths.default_classification(anchor=ROOT),
            REAL_TAX / TABLE,
        )

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
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(via_env)}):
                self.assertEqual(
                    tol_paths.resolve_onto(explicit, anchor=anchor),
                    explicit.resolve(),
                )
                self.assertEqual(tol_paths.resolve_onto(anchor=anchor), via_env.resolve())
            with _cleared_roots():
                self.assertEqual(
                    tol_paths.resolve_onto(anchor=anchor),
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
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(via_env)}):
                with self.assertRaises(tol_paths.RootError) as raised:
                    tol_paths.resolve_onto("   ", anchor=anchor)
                self.assertIn("ONTO_ROOT", str(raised.exception))
                self.assertNotIn(str(via_env), str(raised.exception))
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: "  \t "}):
                self.assertEqual(
                    tol_paths.resolve_onto(anchor=anchor),
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
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(bad)}):
                with self.assertRaises(tol_paths.RootError) as raised:
                    tol_paths.resolve_onto(anchor=anchor)
            message = str(raised.exception)
            self.assertIn("ONTO_ROOT", message)
            self.assertIn(str(bad.resolve()), message)
            self.assertIn(MARKER, message)

    def test_missing_checkout_names_each_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "alone" / "tool.py", "")
            with _cleared_roots():
                with self.assertRaises(tol_paths.RootError) as onto_error:
                    tol_paths.resolve_onto(anchor=anchor)
                with self.assertRaises(tol_paths.RootError) as tax_error:
                    tol_paths.resolve_taxontoy(anchor=anchor)
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
                self.assertEqual(tol_paths.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())
                self.assertEqual(
                    tol_paths.resolve_taxontoy(anchor=anchor),
                    (project / "TaxOntOy").resolve(),
                )
            # Grandchild: markers live on an ancestor, not beside this directory.
            nested = root / "parent" / "vendor" / "TopOntoLogy"
            _touch(root / "parent" / "ONTO" / MARKER, "ancestor\n")
            _touch(root / "parent" / "TaxOntOy" / TABLE, "{}\n")
            deep = _touch(nested / "topontology.py", "")
            with _cleared_roots():
                self.assertEqual(
                    tol_paths.infer_checkout("ONTO", MARKER, deep),
                    (root / "parent" / "ONTO").resolve(),
                )
                self.assertEqual(
                    tol_paths.resolve_taxontoy(anchor=nested),
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
                self.assertEqual(tol_paths.resolve_onto(anchor=anchor), (root / "ONTO").resolve())

    def test_a_directory_marker_is_not_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decoy = root / "ONTO" / MARKER
            decoy.mkdir(parents=True)
            anchor = _touch(root / "TopOntoLogy" / "tool.py", "")
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: ""}):
                os.environ.pop(tol_paths.ONTO_ENV, None)
                with self.assertRaises(tol_paths.RootError) as raised:
                    tol_paths.resolve_onto(anchor=anchor)
            self.assertIn("ONTO_ROOT", str(raised.exception))

    def test_tilde_in_an_explicit_root_is_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "empty" / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), tol_paths.ONTO_ENV: ""}):
                os.environ.pop(tol_paths.ONTO_ENV, None)
                self.assertEqual(tol_paths.resolve_onto("~/ONTO", anchor=anchor), onto.resolve())


class OriginTests(unittest.TestCase):
    def test_install_is_first_and_idempotent_and_origin_must_match(self):
        entry = str(REAL_ONTO)
        saved_path = list(sys.path)
        saved_module = sys.modules.get("knowledgegraph")
        try:
            while entry in sys.path:
                sys.path.remove(entry)
            tol_paths.install_onto(REAL_ONTO)
            self.assertEqual(sys.path[0], entry)
            tol_paths.install_onto(REAL_ONTO)
            self.assertEqual(sys.path.count(entry), 1)
            self.assertEqual(tol_paths.verify_onto_modules(REAL_ONTO), REAL_ONTO)
            sys.modules.pop("knowledgegraph", None)
            self.assertEqual(tol_paths.verify_onto_modules(REAL_ONTO), REAL_ONTO)
            missing = types.ModuleType("knowledgegraph")
            missing.__file__ = None
            sys.modules["knowledgegraph"] = missing
            with self.assertRaises(tol_paths.RootError) as raised:
                tol_paths.verify_onto_modules(REAL_ONTO)
            self.assertIn("(なし)", str(raised.exception))
            self.assertIn("ONTO_ROOT", str(raised.exception))
            wrong = types.ModuleType("knowledgegraph")
            wrong.__file__ = str(Path("/tmp") / MARKER)
            sys.modules["knowledgegraph"] = wrong
            with self.assertRaises(tol_paths.RootError) as raised:
                tol_paths.verify_onto_modules(REAL_ONTO)
            self.assertIn(str(Path("/tmp") / MARKER), str(raised.exception))
        finally:
            sys.path[:] = saved_path
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module

    def test_configure_records_roots_without_clobbering_env(self):
        with patch.dict(os.environ, {tol_paths.ONTO_ENV: "keep-onto", tol_paths.TAXONTOY_ENV: "keep-tax"}):
            onto, tax = tol_paths.configure(
                ROOT, explicit_onto=REAL_ONTO, explicit_taxontoy=REAL_TAX,
            )
            self.assertEqual(onto, REAL_ONTO)
            self.assertEqual(tax, REAL_TAX)
            self.assertEqual(os.environ[tol_paths.ONTO_ENV], "keep-onto")
            self.assertEqual(os.environ[tol_paths.TAXONTOY_ENV], "keep-tax")
            self.assertEqual(tol_paths.BOOTED_ONTO, REAL_ONTO)
            self.assertEqual(tol_paths.BOOTED_TAXONTOY, REAL_TAX)
        saved = {}
        for name in (tol_paths.ONTO_ENV, tol_paths.TAXONTOY_ENV):
            saved[name] = os.environ.pop(name, None)
        try:
            tol_paths.configure(ROOT)
            self.assertEqual(os.environ[tol_paths.ONTO_ENV], str(REAL_ONTO))
            self.assertEqual(os.environ[tol_paths.TAXONTOY_ENV], str(REAL_TAX))
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class ArgvTests(unittest.TestCase):
    def test_flags_forms_stop_and_missing_values(self):
        self.assertEqual(tol_paths.explicit_from_argv([]), (None, None))
        self.assertEqual(
            tol_paths.explicit_from_argv(["graph", "--onto-root", "/onto", "--taxontoy-root", "/tax"]),
            ("/onto", "/tax"),
        )
        self.assertEqual(
            tol_paths.explicit_from_argv(["--onto-root=/onto", "--taxontoy-root=/tax", "audit"]),
            ("/onto", "/tax"),
        )
        self.assertEqual(
            tol_paths.explicit_from_argv(["--onto-root", "/first", "--onto-root", "/second"]),
            ("/second", None),
        )
        self.assertEqual(
            tol_paths.explicit_from_argv(["--", "--onto-root", "/ignored"]),
            (None, None),
        )
        self.assertEqual(tol_paths.explicit_from_argv(["--onto-root-extra", "/nope"]), (None, None))
        self.assertEqual(tol_paths.explicit_from_argv(["--onto-root="]), ("", None))
        with self.assertRaises(tol_paths.RootError) as raised:
            tol_paths.explicit_from_argv(["--onto-root"])
        self.assertIn("ONTO_ROOT", str(raised.exception))
        with self.assertRaises(tol_paths.RootError) as raised:
            tol_paths.explicit_from_argv(["--taxontoy-root"])
        self.assertIn("TAXONTOY_ROOT", str(raised.exception))


class CliResolutionTests(unittest.TestCase):
    def test_short_form_infers_the_classification(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "concepts",
            env={"ONTO_ROOT": None, "TAXONTOY_ROOT": None, "PYTHONPATH": None},
        )
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertTrue(body["provenance"]["classification"]["path"].endswith("TaxOntOy/otl-relations.json"))
        with _cleared_roots():
            digest = tol_paths.default_classification(anchor=ROOT).read_bytes()
        self.assertEqual(body["provenance"]["classification"]["sha256"], hashlib.sha256(digest).hexdigest())
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
                env={tol_paths.ONTO_ENV: str(REAL_ONTO), tol_paths.TAXONTOY_ENV: str(REAL_TAX)},
            )
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("ONTO_ROOT", err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts",
                env={tol_paths.ONTO_ENV: str(bad), tol_paths.TAXONTOY_ENV: str(REAL_TAX), "PYTHONPATH": None},
            )
            self.assertEqual(code, 1)
            self.assertIn("ONTO_ROOT", err)
            self.assertIn(str(bad.resolve()), err)
            self.assertNotIn("Traceback", err)
            code, out, err = run_cli(
                "tests/fixtures/graph.json", "concepts",
                "--onto-root", str(REAL_ONTO),
                "--taxontoy-root", str(REAL_TAX),
                env={tol_paths.ONTO_ENV: str(bad), tol_paths.TAXONTOY_ENV: str(bad)},
            )
            self.assertEqual(code, 0, err)
            code, out, err = run_cli("--onto-root")
            self.assertEqual(code, 1)
            self.assertIn("ONTO_ROOT", err)
            self.assertNotIn("Traceback", err)

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
                env={"PYTHONPATH": str(fake), tol_paths.ONTO_ENV: str(REAL_ONTO)},
            )
            self.assertEqual(code, 0, err)
            self.assertNotIn("WRONG_ONTO", err)
            self.assertNotIn("WRONG_ONTO", out)

    def test_library_boot_reraises_a_bad_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: tmp}):
                with self.assertRaises(tol_paths.RootError) as raised:
                    topontology._boot()
            self.assertIn("ONTO_ROOT", str(raised.exception))
            self.assertIn(str(Path(tmp).resolve()), str(raised.exception))


class ConftestTests(unittest.TestCase):
    def test_usage_error_names_the_env_var_and_checks_origin(self):
        stub = types.ModuleType("pytest")

        class UsageError(Exception):
            pass

        stub.UsageError = UsageError
        saved_pytest = sys.modules.get("pytest")
        saved_module = sys.modules.get("knowledgegraph")
        sys.modules["pytest"] = stub
        try:
            spec = importlib.util.spec_from_file_location("tol_conftest_under_test", ROOT / "conftest.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            onto, tax = module.activate()
            self.assertEqual(onto, REAL_ONTO)
            self.assertEqual(tax, REAL_TAX)
            with _cleared_roots():
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(UsageError) as raised:
                        module.activate(Path(tmp) / "missing.py")
                    self.assertIn("ONTO_ROOT", str(raised.exception))
                    fake = types.ModuleType("knowledgegraph")
                    fake.__file__ = str(Path(tmp) / MARKER)
                    sys.modules["knowledgegraph"] = fake
                    with self.assertRaises(UsageError) as raised:
                        module.activate()
                    self.assertIn("出どころ", str(raised.exception))
                    self.assertIn("ONTO_ROOT", str(raised.exception))
        finally:
            if saved_pytest is None:
                sys.modules.pop("pytest", None)
            else:
                sys.modules["pytest"] = saved_pytest
            if saved_module is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved_module


class ResolutionMutationTests(unittest.TestCase):
    def test_swapped_explicit_and_env_is_killed(self):
        mutant = load_tol_mutant(
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
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(via_env)}):
                self.assertEqual(tol_paths.resolve_onto(explicit, anchor=anchor), explicit.resolve())
                self.assertEqual(mutant.resolve_onto(explicit, anchor=anchor), via_env.resolve())

    def test_blank_explicit_falling_through_is_killed(self):
        mutant = load_tol_mutant(
            "    if not text:\n        return \"\"\n",
            "    if not text:\n        return None\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            via_env = Path(tmp) / "env"
            _touch(via_env / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(via_env)}):
                with self.assertRaises(tol_paths.RootError):
                    tol_paths.resolve_onto(" ", anchor=anchor)
                self.assertEqual(mutant.resolve_onto(" ", anchor=anchor), via_env.resolve())

    def test_skipping_the_marker_check_is_killed(self):
        mutant = load_tol_mutant(
            "    if not (path / marker).is_file():\n",
            "    if False and not (path / marker).is_file():\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "empty"
            bad.mkdir()
            with self.assertRaises(tol_paths.RootError):
                tol_paths.resolve_onto(bad, anchor=Path(tmp))
            self.assertEqual(mutant.resolve_onto(bad, anchor=Path(tmp)), bad.resolve())

    def test_dropping_the_env_name_from_the_message_is_killed(self):
        mutant = load_tol_mutant(
            '    return f"{label} が見つからない。{env_name}= を渡す（{marker} がある checkout）"\n',
            '    return f"{label} が見つからない。checkout を渡す（{marker}）"\n',
        )
        with tempfile.TemporaryDirectory() as tmp:
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: ""}):
                os.environ.pop(tol_paths.ONTO_ENV, None)
                with self.assertRaises(tol_paths.RootError) as real:
                    tol_paths.resolve_onto(anchor=anchor)
                with self.assertRaises(mutant.RootError) as changed:
                    mutant.resolve_onto(anchor=anchor)
        self.assertIn("ONTO_ROOT=", str(real.exception))
        self.assertNotIn("ONTO_ROOT", str(changed.exception))

    def test_skipping_the_origin_check_is_killed(self):
        mutant = load_tol_mutant(
            "    root = Path(root).resolve()\n    problems = []\n",
            "    return Path(root).resolve()\n    problems = []\n",
        )
        saved = sys.modules.get("knowledgegraph")
        try:
            wrong = types.ModuleType("knowledgegraph")
            wrong.__file__ = "/tmp/knowledgegraph.py"
            sys.modules["knowledgegraph"] = wrong
            with self.assertRaises(tol_paths.RootError):
                tol_paths.verify_onto_modules(REAL_ONTO)
            self.assertEqual(mutant.verify_onto_modules(REAL_ONTO), REAL_ONTO)
        finally:
            if saved is None:
                sys.modules.pop("knowledgegraph", None)
            else:
                sys.modules["knowledgegraph"] = saved

    def test_not_walking_ancestors_is_killed(self):
        mutant = load_tol_mutant(
            "    for candidate in (here, *here.parents):\n",
            "    for candidate in (here,):\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "parent" / "vendor" / "TopOntoLogy"
            _touch(root / "parent" / "ONTO" / MARKER)
            anchor = _touch(nested / "tool.py", "")
            self.assertEqual(
                tol_paths.infer_checkout("ONTO", MARKER, anchor),
                (root / "parent" / "ONTO").resolve(),
            )
            self.assertIsNone(mutant.infer_checkout("ONTO", MARKER, anchor))

    def test_preferring_the_nested_copy_is_killed(self):
        mutant = load_tol_mutant(
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
            self.assertEqual(tol_paths.infer_checkout("ONTO", MARKER, anchor), (root / "ONTO").resolve())
            self.assertEqual(mutant.infer_checkout("ONTO", MARKER, anchor), (project / "ONTO").resolve())

    def test_invalid_env_falling_through_to_inference_is_killed(self):
        mutant = load_tol_mutant(
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
            with patch.dict(os.environ, {tol_paths.ONTO_ENV: str(bad)}):
                with self.assertRaises(tol_paths.RootError):
                    tol_paths.resolve_onto(anchor=anchor)
                self.assertEqual(
                    mutant.resolve_onto(anchor=anchor),
                    (parent / "ONTO").resolve(),
                )

    def test_appending_onto_to_sys_path_is_killed(self):
        mutant = load_tol_mutant(
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
            placed = mutant.install_onto(REAL_ONTO)
            self.assertEqual(placed, entry)
            self.assertLess(sys.path.index(decoy), sys.path.index(entry))
            while entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, decoy)
            self.assertEqual(tol_paths.install_onto(REAL_ONTO), entry)
            self.assertEqual(sys.path[0], entry)
        finally:
            sys.path[:] = saved

    def test_overwriting_a_preset_root_env_is_killed(self):
        mutant = load_tol_mutant(
            "    os.environ.setdefault(ONTO_ENV, str(onto))\n",
            "    os.environ[ONTO_ENV] = str(onto)\n",
        )
        with patch.dict(os.environ, {tol_paths.ONTO_ENV: "keep-onto", tol_paths.TAXONTOY_ENV: str(REAL_TAX)}):
            tol_paths.configure(ROOT, explicit_onto=REAL_ONTO, explicit_taxontoy=REAL_TAX)
            self.assertEqual(os.environ[tol_paths.ONTO_ENV], "keep-onto")
            mutant.configure(ROOT, explicit_onto=REAL_ONTO, explicit_taxontoy=REAL_TAX)
            self.assertEqual(os.environ[tol_paths.ONTO_ENV], str(REAL_ONTO))

    def test_ignoring_the_equals_form_is_killed(self):
        mutant = load_tol_mutant(
            "            if token.startswith(prefix):\n",
            "            if False and token.startswith(prefix):\n",
        )
        argv = ["--onto-root=/onto", "--taxontoy-root=/tax"]
        self.assertEqual(tol_paths.explicit_from_argv(argv), ("/onto", "/tax"))
        self.assertEqual(mutant.explicit_from_argv(argv), (None, None))

    def test_skipping_tilde_expansion_is_killed(self):
        mutant = load_tol_mutant(
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).expanduser().resolve()\n",
            "def _require_marker(root, marker, env_name, label):\n    path = Path(root).resolve()\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            onto = home / "ONTO"
            _touch(onto / MARKER)
            anchor = _touch(Path(tmp) / "tool.py", "")
            with patch.dict(os.environ, {"HOME": str(home), tol_paths.ONTO_ENV: ""}):
                os.environ.pop(tol_paths.ONTO_ENV, None)
                self.assertEqual(tol_paths.resolve_onto("~/ONTO", anchor=anchor), onto.resolve())
                with self.assertRaises(mutant.RootError):
                    mutant.resolve_onto("~/ONTO", anchor=anchor)


if __name__ == "__main__":
    unittest.main()
