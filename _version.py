"""Single source of truth for the Echelon version.

pyproject.toml reads this file ([tool.hatch.version]), and the CLI reports it
via ``echelon --version``. Bump here; everything else follows.
"""

__version__ = "0.3.0"
