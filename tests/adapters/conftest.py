"""Fixtures for the real-BayBE adapter contract tests.

These tests exercise the production :class:`~backend.adapters.baybe.BayBEAdapter`
against *real* BayBE — nothing is mocked. BayBE is vendored (and git-ignored) at
``<repo>/baybe`` and is not pip-installed, so its clone root is placed ahead of
the repo root on ``sys.path`` here (before the adapter is imported) so that
``import baybe`` resolves to ``<repo>/baybe/baybe`` rather than the empty
namespace directory that would otherwise shadow it.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDORED_BAYBE_ROOT = _REPO_ROOT / "baybe"

if (_VENDORED_BAYBE_ROOT / "baybe" / "__init__.py").exists():
    _path = str(_VENDORED_BAYBE_ROOT)
    if _path not in sys.path:
        sys.path.insert(0, _path)
    # Drop any namespace-shadow import so the real package resolves cleanly.
    for _name in [n for n in sys.modules if n == "baybe" or n.startswith("baybe.")]:
        del sys.modules[_name]

# Skip the whole contract suite if real BayBE genuinely cannot be imported,
# rather than reporting a misleading collection error.
pytest.importorskip("baybe.parameters")

from backend.adapters.baybe import BayBEAdapter  # noqa: E402


@pytest.fixture
def baybe_adapter() -> BayBEAdapter:
    """Return a fresh production BayBE adapter."""
    return BayBEAdapter()
