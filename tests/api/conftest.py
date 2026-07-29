"""Fixtures for the HTTP API tests.

The API is exercised end-to-end through Starlette's ``TestClient`` against a
real, request-scoped SQLite file (so state persists across requests within a
test) and the *real* vendored BayBE adapter — nothing is mocked. BayBE is
vendored (git-ignored) at ``<repo>/baybe``; its clone root is placed ahead of
the repo root on ``sys.path`` before the adapter is imported so ``import baybe``
resolves to the real package rather than the empty namespace shadow.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENDORED_BAYBE_ROOT = _REPO_ROOT / "baybe"

if (_VENDORED_BAYBE_ROOT / "baybe" / "__init__.py").exists():
    _path = str(_VENDORED_BAYBE_ROOT)
    if _path not in sys.path:
        sys.path.insert(0, _path)
    for _name in [n for n in sys.modules if n == "baybe" or n.startswith("baybe.")]:
        del sys.modules[_name]

pytest.importorskip("baybe.parameters")

from backend.adapters.baybe import BayBEAdapter  # noqa: E402
from backend.api import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path) -> TestClient:
    """A TestClient over a fresh file-backed app wired to real BayBE."""
    app = create_app(db_path=str(tmp_path / "api-test.db"), adapter=BayBEAdapter())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    """The minimal actor header every mutating request needs."""
    return {"X-Actor-Id": "user-1"}


@pytest.fixture
def make_payload():
    """Return the :func:`create_payload` builder for use inside tests."""
    return create_payload


def create_payload(**overrides) -> dict:
    """Return a valid ``POST /campaign-runs`` body (all-continuous, Random)."""
    payload = {
        "name": "Epoxy Coating Optimization",
        "goal": "maximize coating strength",
        "parameters": [
            {
                "type": "Continuous",
                "id": "resin",
                "name": "Resin",
                "bounds": {"lower": 0, "upper": 100},
            },
            {
                "type": "Continuous",
                "id": "hard",
                "name": "Hardener",
                "bounds": {"lower": 0, "upper": 100},
            },
        ],
        "outputs": [{"id": "o1", "name": "Strength"}],
        "targets": [{"id": "t1", "outputId": "o1", "direction": "Maximize"}],
        "objectivePolicy": {"kind": "Single", "targetId": "t1"},
        "constraints": [],
        "constraintsConfirmed": True,
        "optimizationPolicy": {
            "backendName": "baybe",
            "batchSize": 3,
            "seedPolicy": "Fixed",
            "seedValue": 42,
            "strategyConfig": {
                "kind": "TwoPhaseMeta",
                "initialRecommender": "RandomRecommender",
                "switchAfter": 5,
                "remainSwitched": True,
                "acquisitionFunction": "qLogEI",
            },
        },
        "budgetTotal": 10,
    }
    payload.update(overrides)
    return payload
