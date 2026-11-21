"""ONTO の onto_path.py を vendors した複製（標準ライブラリだけ）。

正本は ONTO の onto_path.py。このファイルはその複製で、TopOntoLogy は新しい版を
自分では追わない。リポジトリごとの値は下の定数だけで、関数の本体はどの複製でも同じ。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# この checkout が解決する根。本体は ROOTS と FLAGS だけを読む。
ONTO_ENV = "ONTO_ROOT"
ONTO_DIRNAME = "ONTO"
ONTO_MARKER = "knowledgegraph.py"
ONTO_MODULES = ("knowledgegraph",)
ONTO_FLAG = "--onto-root"

TAXONTOY_ENV = "TAXONTOY_ROOT"
TAXONTOY_DIRNAME = "TaxOntOy"
TAXONTOY_MARKER = "otl-relations.json"
TAXONTOY_MODULES = ()
TAXONTOY_FLAG = "--taxontoy-root"

ROOTS = {
    "ONTO": {
        "env": ONTO_ENV,
        "dirname": ONTO_DIRNAME,
        "marker": ONTO_MARKER,
        "modules": ONTO_MODULES,
    },
    "TaxOntOy": {
        "env": TAXONTOY_ENV,
        "dirname": TAXONTOY_DIRNAME,
        "marker": TAXONTOY_MARKER,
        "modules": TAXONTOY_MODULES,
    },
}

FLAGS = (
    (ONTO_FLAG, "ONTO"),
    (TAXONTOY_FLAG, "TaxOntOy"),
)

RESOLVED = {}


class RootError(FileNotFoundError):
    """Checkout を決められない。メッセージは環境変数の名前を含む。"""


def _missing(label, env_name, marker):
    return f"{label} が見つからない。{env_name}= を渡す（{marker} がある checkout）"


def _unmarked(label, env_name, marker, root):
    return f"{label} が見つからない（{root} に {marker} がない）。{env_name}= を渡す"


def _wrong_origin(problems, env_name):
    return "モジュールの出どころが不正（" + ", ".join(problems) + f"）。{env_name}= を渡す"


def _flag_needs_value(flag, env_name):
    return f"{flag} の値が無い。{env_name}= を渡す"


def _spec(name):
    try:
        return ROOTS[name]
    except KeyError:
        known = ", ".join(ROOTS)
        raise RootError(f"未知の checkout 名: {name}（{known}）") from None


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


def _resolve(explicit, *, env_name, dirname, marker, label, anchor=None):
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


def resolve_root(name, explicit=None, *, anchor=None):
    """``name`` は ROOTS のキー。明示、環境変数、隣、上への探索の順。"""
    spec = _spec(name)
    return _resolve(
        explicit,
        env_name=spec["env"],
        dirname=spec["dirname"],
        marker=spec["marker"],
        label=name,
        anchor=anchor,
    )


def marker_file(name, explicit=None, *, anchor=None):
    """解決した根にあるマーカーファイル。"""
    return resolve_root(name, explicit, anchor=anchor) / _spec(name)["marker"]


def install_root(root):
    """根を sys.path の先頭に置く。"""
    entry = str(Path(root).resolve())
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    return entry


def verify_origin(root, modules, env_name):
    """import 済みモジュールが ``root`` のものか確かめる。"""
    root = Path(root).resolve()
    problems = []
    for name in modules:
        module = sys.modules.get(name)
        if module is None:
            module = __import__(name)
        origin = getattr(module, "__file__", None)
        if origin is None or Path(origin).resolve().parent != root:
            shown = origin if origin is not None else "(なし)"
            problems.append(f"{name}={shown}")
    if problems:
        raise RootError(_wrong_origin(problems, env_name))
    return root


def bind(anchor, explicit=None):
    """ROOTS の各根を決め、環境変数を足し、モジュールのある根は出どころを確かめる。"""
    chosen = explicit or {}
    found = {}
    for name, spec in ROOTS.items():
        root = resolve_root(name, chosen.get(name), anchor=anchor)
        os.environ.setdefault(spec["env"], str(root))
        if spec["modules"]:
            install_root(root)
            verify_origin(root, spec["modules"], spec["env"])
        found[name] = root
    RESOLVED.clear()
    RESOLVED.update(found)
    return dict(found)


def explicit_from_argv(argv):
    """FLAGS の旗。同じ旗が複数なら最後を使う。値は根の名前から引く。"""
    found = {name: None for _flag, name in FLAGS}
    index = 0
    args = list(argv)
    while index < len(args):
        token = args[index]
        if token == "--":
            break
        handled = False
        for flag, name in FLAGS:
            env_name = ROOTS[name]["env"]
            if token == flag:
                if index + 1 >= len(args):
                    raise RootError(_flag_needs_value(flag, env_name))
                found[name] = args[index + 1]
                index += 2
                handled = True
                break
            prefix = flag + "="
            if token.startswith(prefix):
                found[name] = token[len(prefix):]
                index += 1
                handled = True
                break
        if not handled:
            index += 1
    return found
