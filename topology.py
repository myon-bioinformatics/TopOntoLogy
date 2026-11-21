"""Backward-compatible import for ``summarize``.

OnTautoLogy ``examples/wave_relations.py`` imports ``summarize`` from this module.
The command-line entry point is ``topontology.py``.
"""
from topontology import summarize

__all__ = ["summarize"]


if __name__ == "__main__":
    import sys

    print("コマンドは topontology.py です。このファイルは from topology import summarize の互換用です。", file=sys.stderr)
    raise SystemExit(2)
