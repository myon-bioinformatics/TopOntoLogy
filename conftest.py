"""pytest 用の入口。検査の正は unittest で、pytest は依存にしない。

単独の checkout でも、親リポジトリの子や孫として埋め込まれていても、
ONTO と TaxOntOy の場所は tol_paths が決める。出どころが違えば黙って続けない。
"""
from pathlib import Path

import pytest

import tol_paths


def activate(anchor=None):
    """ONTO_ROOT と TAXONTOY_ROOT を入れ、見つからなければ UsageError にする。"""
    if anchor is None:
        anchor = Path(__file__)
    try:
        return tol_paths.configure(anchor)
    except tol_paths.RootError as error:
        raise pytest.UsageError(str(error)) from None


activate()
