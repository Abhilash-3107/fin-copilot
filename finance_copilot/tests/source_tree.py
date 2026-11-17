"""Tell a checkout apart from a copy of this folder run against the installed wheel.

The release job copies this folder outside the repository and runs it with the
wheel's Python, so a guard that reads repository files (ui/, scripts/, the
sources it scans) has nothing to read there. Those guards skip, with a reason,
only when the tests are not inside a checkout. In a checkout a missing file
still fails.
"""
from __future__ import annotations

from pathlib import Path

NOT_A_CHECKOUT = (
    "needs the source tree (these tests are running from a copy outside the "
    "repository, against the installed wheel)"
)


def source_root() -> Path | None:
    """The repository root when this file sits at <root>/finance_copilot/tests, else None."""
    here = Path(__file__).resolve().parent
    root = here.parents[1]
    in_checkout = (root / "pyproject.toml").is_file() and here == root / "finance_copilot" / "tests"
    return root if in_checkout else None
