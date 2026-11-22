"""Forget root environment variables before the suite imports the tool.

Resolution stays explicit option, then environment, then a sibling, then
walk-up. A wrong ``ONTO_ROOT`` would fail that import even when the sibling
checkout is valid, so the suite does not keep those variables.
"""
import os

import onto_path

for _name in (onto_path.ONTO_ENV, onto_path.TAXONTOY_ENV, onto_path.OTL_ENV):
    os.environ.pop(_name, None)
