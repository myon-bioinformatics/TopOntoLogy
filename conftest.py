"""pytest 用の入口。検査の正は unittest で、pytest は依存にしない。

単独の checkout でも、親リポジトリの子や孫として埋め込まれていても、
ONTO と TaxOntOy と OnTautoLogy の場所は onto_path が決める。出どころが違えば黙って続けない。
"""
import os
from pathlib import Path

import pytest

import onto_path

# 誤った ONTO_ROOT などが、隣の checkout より先に import を止めないようにする。
for _name in (onto_path.ONTO_ENV, onto_path.TAXONTOY_ENV, onto_path.OTL_ENV):
    os.environ.pop(_name, None)

# 裸の pytest が、隣の checkout や build をこのリポジトリの検査として集めない。
collect_ignore = ["ONTO", "TaxOntOy", "OnTautoLogy", "otl", "build"]


def activate(anchor=None):
    """ONTO_ROOT と TAXONTOY_ROOT と OTL_ROOT を入れ、見つからなければ UsageError にする。"""
    if anchor is None:
        anchor = Path(__file__)
    try:
        return onto_path.bind(anchor)
    except onto_path.RootError as error:
        raise pytest.UsageError(str(error)) from None


activate()
