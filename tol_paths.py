"""TopOntoLogy の配置（標準ライブラリだけ）。ONTO と TaxOntOy の場所を知る唯一のモジュール。

解決の順は、明示の引数、環境変数、推定。推定は TopOntoLogy の隣の checkout を先に見て、
なければアンカーから上へ辿り、マーカーのあるディレクトリを探す。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# レイアウトの定数はここだけ。スキーマ名やプロファイルは topontology.py の先頭に置く。
ONTO_ENV = "ONTO_ROOT"
TAXONTOY_ENV = "TAXONTOY_ROOT"
ONTO_DIRNAME = "ONTO"
TAXONTOY_DIRNAME = "TaxOntOy"
ONTO_MARKER = "knowledgegraph.py"
TAXONTOY_MARKER = "otl-relations.json"
ONTO_MODULES = ("knowledgegraph",)

BOOTED_ONTO = None
BOOTED_TAXONTOY = None


class RootError(FileNotFoundError):
    """Checkout を決められない。メッセージは環境変数の名前を含む。"""


def _missing(label, env_name, marker):
    return f"{label} が見つからない。{env_name}= を渡す（{marker} がある checkout）"


def _unmarked(label, env_name, marker, root):
    return f"{label} が見つからない（{root} に {marker} がない）。{env_name}= を渡す"


def _wrong_origin(problems):
    return "ONTO のモジュールの出どころが不正（" + ", ".join(problems) + f"）。{ONTO_ENV}= を渡す"


def _flag_needs_value(flag, env_name):
    return f"{flag} の値が無い。{env_name}= を渡す"


def _anchor_dir(anchor):
    if anchor is None:
        anchor = Path(__file__)
    here = Path(anchor).expanduser().resolve()
    if here.is_file():
        return here.parent
    return here


def _dir_if_marked(directory, marker):
    directory = Path(directory).expanduser().resolve()
    if (directory / marker).is_file():
        return directory
    return None


def infer_checkout(dirname, marker, anchor=None):
    """隣の checkout を先に見て、なければ上へ辿ってマーカーを探す。"""
    here = _anchor_dir(anchor)
    found = _dir_if_marked(here.parent / dirname, marker)
    if found is not None:
        return found
    seen = {here.parent.resolve()}
    for candidate in (here, *here.parents):
        found = _dir_if_marked(candidate / dirname, marker)
        if found is not None:
            return found
        parent = candidate.resolve().parent
        if parent not in seen:
            seen.add(parent)
            found = _dir_if_marked(parent / dirname, marker)
            if found is not None:
                return found
    return None


def _explicit_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    return text


def _env_text(env_name):
    raw = os.environ.get(env_name)
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return text


def _require_marker(root, marker, env_name, label):
    path = Path(root).expanduser().resolve()
    if not (path / marker).is_file():
        raise RootError(_unmarked(label, env_name, marker, path))
    return path


def resolve_root(explicit, *, env_name, dirname, marker, label, anchor=None):
    """明示の引数、環境変数、推定。空の明示は環境変数へ落とさない。"""
    chosen = _explicit_text(explicit)
    if chosen is not None:
        if chosen == "":
            raise RootError(_missing(label, env_name, marker))
        return _require_marker(chosen, marker, env_name, label)
    chosen = _env_text(env_name)
    if chosen is not None:
        return _require_marker(chosen, marker, env_name, label)
    found = infer_checkout(dirname, marker, anchor)
    if found is None:
        raise RootError(_missing(label, env_name, marker))
    return _require_marker(found, marker, env_name, label)


def resolve_onto(explicit=None, *, anchor=None):
    return resolve_root(
        explicit,
        env_name=ONTO_ENV,
        dirname=ONTO_DIRNAME,
        marker=ONTO_MARKER,
        label="ONTO",
        anchor=anchor,
    )


def resolve_taxontoy(explicit=None, *, anchor=None):
    return resolve_root(
        explicit,
        env_name=TAXONTOY_ENV,
        dirname=TAXONTOY_DIRNAME,
        marker=TAXONTOY_MARKER,
        label="TaxOntOy",
        anchor=anchor,
    )


def default_classification(explicit=None, *, anchor=None):
    return resolve_taxontoy(explicit, anchor=anchor) / TAXONTOY_MARKER


def install_onto(root):
    """ONTO を sys.path の先頭に置く。PYTHONPATH より先に読む。"""
    entry = str(Path(root).resolve())
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    return entry


def verify_onto_modules(root):
    """import 済みの ONTO モジュールが、解決した checkout のものか確かめる。"""
    root = Path(root).resolve()
    problems = []
    for name in ONTO_MODULES:
        module = sys.modules.get(name)
        if module is None:
            module = __import__(name)
        origin = getattr(module, "__file__", None)
        if origin is None or Path(origin).resolve().parent != root:
            shown = origin if origin is not None else "(なし)"
            problems.append(f"{name}={shown}")
    if problems:
        raise RootError(_wrong_origin(problems))
    return root


def configure(anchor, *, explicit_onto=None, explicit_taxontoy=None):
    """二つの根を決め、ONTO を読み込めるようにし、出どころを確かめる。"""
    global BOOTED_ONTO, BOOTED_TAXONTOY
    onto = resolve_onto(explicit_onto, anchor=anchor)
    tax = resolve_taxontoy(explicit_taxontoy, anchor=anchor)
    os.environ.setdefault(ONTO_ENV, str(onto))
    os.environ.setdefault(TAXONTOY_ENV, str(tax))
    install_onto(onto)
    verify_onto_modules(onto)
    BOOTED_ONTO = onto
    BOOTED_TAXONTOY = tax
    return onto, tax


def explicit_from_argv(argv):
    """``--onto-root`` と ``--taxontoy-root``。同じ旗が複数なら最後を使う。"""
    onto = None
    tax = None
    index = 0
    args = list(argv)
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        handled = False
        for flag, env_name, slot in (
            ("--onto-root", ONTO_ENV, "onto"),
            ("--taxontoy-root", TAXONTOY_ENV, "tax"),
        ):
            if token == flag:
                if index + 1 >= len(args):
                    raise RootError(_flag_needs_value(flag, env_name))
                value = args[index + 1]
                if slot == "onto":
                    onto = value
                else:
                    tax = value
                index += 2
                handled = True
                break
            prefix = flag + "="
            if token.startswith(prefix):
                value = token[len(prefix):]
                if slot == "onto":
                    onto = value
                else:
                    tax = value
                index += 1
                handled = True
                break
        if not handled:
            index += 1
    return onto, tax
