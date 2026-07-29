"""App-factory and error-sanitization tests (architecture v0.2, §6).

Two guarantees are pinned here that the happy-path suite does not cover: the
factory refuses an in-memory database, and an adapter computation failure never
leaks its raw message (or any batch/round/experiment) to the client.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.application.adapter import RecommendationResult
from backend.adapters.errors import AdapterComputationError
from backend.domain.models import CampaignDefinitionRevision, OptimizationPolicy


def test_create_app_rejects_in_memory_path() -> None:
    with pytest.raises(ValueError, match="file-backed"):
        create_app(db_path=":memory:")


class _ExplodingAdapter:
    """An adapter whose initial-design leg fails with a secret-bearing message."""

    secret = "SECRET_INTERNAL_BAYBE_DETAIL"

    def generate_initial_design(
        self,
        revision: CampaignDefinitionRevision,
        policy: OptimizationPolicy,
    ) -> RecommendationResult:
        raise AdapterComputationError(self.secret)


def test_adapter_computation_failure_is_sanitized(
    tmp_path, headers, make_payload
) -> None:
    app = create_app(
        db_path=str(tmp_path / "leak-test.db"), adapter=_ExplodingAdapter()
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/campaign-runs", json=make_payload(), headers=headers
        )
        run_id = created.json()["campaignRun"]["id"]
        client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)

        response = client.post(
            f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
        )

        assert response.status_code == 502
        body = response.json()
        assert body["code"] == "ADAPTER_COMPUTATION_FAILED"
        assert body["message"] == (
            "The optimization backend failed to generate a design."
        )
        # The raw backend detail must not appear anywhere in the response.
        assert _ExplodingAdapter.secret not in response.text

        # A failed generation leaves no batch, round, or experiment behind.
        view = client.get(f"/api/v1/campaign-runs/{run_id}").json()
        assert view["recommendationBatches"] == []
        assert view["experimentRounds"] == []
        assert view["experimentRuns"] == []
