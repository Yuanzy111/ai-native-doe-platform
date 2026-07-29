"""End-to-end HTTP tests for the campaign-run API (architecture v0.2, §6).

Every test drives the real Starlette ``TestClient`` against a request-scoped
SQLite file and the real vendored BayBE adapter — nothing is mocked. The suite
covers the item-11 contract: the created status, atomic create, the validation
toggle, a real initial-design generation and its read-back, and each mapped
error (409 duplicate, 422 backend mismatch with no data, 404 unknown run, 422
missing actor). ``make_payload`` (from ``conftest``) yields a valid request-body
builder.
"""

from __future__ import annotations


def _create_validated_run(client, headers, make_payload) -> str:
    """Create a run and advance it to ``DesignSpaceValidated``; return its id."""
    created = client.post(
        "/api/v1/campaign-runs", json=make_payload(), headers=headers
    )
    assert created.status_code == 201
    run_id = created.json()["campaignRun"]["id"]
    validated = client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)
    assert validated.status_code == 200
    assert validated.json()["campaignRun"]["status"] == "DesignSpaceValidated"
    return run_id


def test_health_ok(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_returns_draft_run(client, headers, make_payload) -> None:
    response = client.post(
        "/api/v1/campaign-runs", json=make_payload(), headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    run = body["campaignRun"]
    assert run["status"] == "Draft"
    assert run["round"] == 0
    assert run["budgetUsed"] == 0
    assert run["budgetTotal"] == 10
    assert run["createdBy"] == "user-1"
    # Server assigns the aggregate-root ids and pins the first revision.
    assert body["campaignDefinition"]["id"] == run["campaignDefinitionId"]
    assert body["pinnedRevision"]["revisionNumber"] == 1
    assert body["pinnedRevision"]["id"] == run["definitionRevisionId"]


def test_create_persists_campaign_revision_and_run_atomically(
    client, headers, make_payload
) -> None:
    response = client.post(
        "/api/v1/campaign-runs", json=make_payload(), headers=headers
    )
    run_id = response.json()["campaignRun"]["id"]

    # A fresh GET reads all three aggregates back from the database.
    fetched = client.get(f"/api/v1/campaign-runs/{run_id}")
    assert fetched.status_code == 200
    view = fetched.json()
    assert view["campaignDefinition"] is not None
    assert view["pinnedRevision"] is not None
    assert view["campaignRun"]["id"] == run_id


def test_valid_validation_advances_to_design_space_validated(
    client, headers, make_payload
) -> None:
    created = client.post(
        "/api/v1/campaign-runs", json=make_payload(), headers=headers
    )
    run_id = created.json()["campaignRun"]["id"]

    response = client.post(
        f"/api/v1/campaign-runs/{run_id}/validate", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["validationResult"]["ok"] is True
    assert body["campaignRun"]["status"] == "DesignSpaceValidated"


def test_invalid_validation_reports_issues_and_stays_draft(
    client, headers, make_payload
) -> None:
    # An unconfirmed constraint set is a blocking validation issue.
    created = client.post(
        "/api/v1/campaign-runs",
        json=make_payload(constraintsConfirmed=False),
        headers=headers,
    )
    run_id = created.json()["campaignRun"]["id"]

    response = client.post(
        f"/api/v1/campaign-runs/{run_id}/validate", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["validationResult"]["ok"] is False
    assert body["validationResult"]["issues"]
    assert body["campaignRun"]["status"] == "Draft"


def test_initial_design_generates_candidates_with_real_baybe(
    client, headers, make_payload
) -> None:
    run_id = _create_validated_run(client, headers, make_payload)

    response = client.post(
        f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
    )
    assert response.status_code == 201
    body = response.json()
    # batchSize is 3 in the default payload.
    assert len(body["recommendationBatch"]["candidates"]) == 3
    assert body["experimentRound"]["status"] == "Open"
    assert len(body["experimentRuns"]) == 3
    assert all(run["status"] == "Pending" for run in body["experimentRuns"])


def test_initial_design_is_readable_via_get(client, headers, make_payload) -> None:
    run_id = _create_validated_run(client, headers, make_payload)
    client.post(f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers)

    view = client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert len(view["recommendationBatches"]) == 1
    assert len(view["experimentRounds"]) == 1
    assert view["experimentRounds"][0]["status"] == "Open"
    pending = view["experimentRuns"]
    assert len(pending) == 3
    assert all(run["status"] == "Pending" for run in pending)


def test_duplicate_initial_design_conflicts(client, headers, make_payload) -> None:
    run_id = _create_validated_run(client, headers, make_payload)
    first = client.post(
        f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
    )
    assert second.status_code == 409
    assert second.json()["code"] == "CONFLICT"


def test_backend_mismatch_is_rejected_and_produces_no_data(
    client, headers, make_payload
) -> None:
    policy = make_payload()["optimizationPolicy"]
    policy["backendName"] = "bofire"
    created = client.post(
        "/api/v1/campaign-runs",
        json=make_payload(optimizationPolicy=policy),
        headers=headers,
    )
    run_id = created.json()["campaignRun"]["id"]
    client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)

    response = client.post(
        f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ADAPTER_VALIDATION_ERROR"

    # The refused generation must leave no batch, round, or experiment behind.
    view = client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert view["recommendationBatches"] == []
    assert view["experimentRounds"] == []
    assert view["experimentRuns"] == []


def test_unknown_run_is_not_found(client) -> None:
    response = client.get("/api/v1/campaign-runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_missing_actor_header_is_rejected(client, make_payload) -> None:
    response = client.post("/api/v1/campaign-runs", json=make_payload())
    assert response.status_code == 422
    assert response.json()["code"] == "MISSING_ACTOR"
