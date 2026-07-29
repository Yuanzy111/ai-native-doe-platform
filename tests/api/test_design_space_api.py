"""End-to-end tests for ``PUT /campaign-runs/{runId}/design-space`` (§3.6).

Drives the real ``TestClient`` against a request-scoped SQLite file. The suite
pins the revision/head/repin lineage, the drop-back-to-Draft rule, the no-op
short-circuit, the post-batch freeze, and the all-or-nothing rollback.
"""

from __future__ import annotations

import copy

from backend.persistence import PersistenceError, SqliteRepository


def _design_payload(make_payload, **overrides) -> dict:
    """Build a design-space body from the shared create payload."""
    base = make_payload()
    payload = {
        "parameters": base["parameters"],
        "outputs": base["outputs"],
        "targets": base["targets"],
        "objectivePolicy": base["objectivePolicy"],
        "constraints": base["constraints"],
        "constraintsConfirmed": base["constraintsConfirmed"],
        "optimizationPolicy": base["optimizationPolicy"],
    }
    payload.update(overrides)
    return payload


def _changed_parameters(make_payload) -> list[dict]:
    """Return the default parameters with one bound widened (a real change)."""
    params = copy.deepcopy(make_payload()["parameters"])
    params[0]["bounds"]["upper"] = 200
    return params


def _create_run(client, headers, make_payload) -> dict:
    """Create a Draft run and return its full aggregate view."""
    response = client.post(
        "/api/v1/campaign-runs", json=make_payload(), headers=headers
    )
    assert response.status_code == 201
    return response.json()


def test_draft_edit_creates_second_revision(client, headers, make_payload) -> None:
    created = _create_run(client, headers, make_payload)
    run_id = created["campaignRun"]["id"]
    original_revision_id = created["pinnedRevision"]["id"]

    body = _design_payload(
        make_payload, parameters=_changed_parameters(make_payload)
    )
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is True
    assert result["revisionChanged"] is True
    assert result["policyChanged"] is False

    pinned = result["view"]["pinnedRevision"]
    assert pinned["revisionNumber"] == 2
    assert pinned["parentRevisionId"] == original_revision_id
    assert result["view"]["campaignDefinition"]["headRevisionId"] == pinned["id"]
    assert pinned["id"] != original_revision_id


def test_get_reflects_new_pinned_revision(client, headers, make_payload) -> None:
    run_id = _create_run(client, headers, make_payload)["campaignRun"]["id"]
    body = _design_payload(
        make_payload, parameters=_changed_parameters(make_payload)
    )
    put = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    new_revision_id = put.json()["view"]["pinnedRevision"]["id"]

    view = client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert view["pinnedRevision"]["id"] == new_revision_id
    assert view["pinnedRevision"]["revisionNumber"] == 2


def test_validated_edit_returns_to_draft(client, headers, make_payload) -> None:
    run_id = _create_run(client, headers, make_payload)["campaignRun"]["id"]
    client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)

    body = _design_payload(
        make_payload, parameters=_changed_parameters(make_payload)
    )
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is True
    assert result["revisionChanged"] is True
    assert result["view"]["campaignRun"]["status"] == "Draft"


def test_policy_only_change_returns_to_draft(client, headers, make_payload) -> None:
    created = _create_run(client, headers, make_payload)
    run_id = created["campaignRun"]["id"]
    original_policy_id = created["campaignRun"]["optimizationPolicy"]["id"]
    client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)

    policy = make_payload()["optimizationPolicy"]
    policy["batchSize"] = 2  # a real policy change, design space untouched
    body = _design_payload(make_payload, optimizationPolicy=policy)
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is True
    assert result["revisionChanged"] is False
    assert result["policyChanged"] is True
    assert result["view"]["campaignRun"]["status"] == "Draft"
    new_policy = result["view"]["campaignRun"]["optimizationPolicy"]
    assert new_policy["batchSize"] == 2
    assert new_policy["id"] != original_policy_id
    # A policy-only change does not append a revision.
    assert result["view"]["pinnedRevision"]["revisionNumber"] == 1


def test_identical_request_is_noop(client, headers, make_payload) -> None:
    created = _create_run(client, headers, make_payload)
    run_id = created["campaignRun"]["id"]
    revision_id = created["pinnedRevision"]["id"]
    policy_id = created["campaignRun"]["optimizationPolicy"]["id"]

    body = _design_payload(make_payload)
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is False
    assert result["revisionChanged"] is False
    assert result["policyChanged"] is False

    view = result["view"]
    assert view["pinnedRevision"]["id"] == revision_id
    assert view["pinnedRevision"]["revisionNumber"] == 1
    assert view["campaignRun"]["optimizationPolicy"]["id"] == policy_id
    assert view["campaignRun"]["status"] == "Draft"


def test_identical_request_keeps_validated_status(
    client, headers, make_payload
) -> None:
    run_id = _create_run(client, headers, make_payload)["campaignRun"]["id"]
    client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)

    body = _design_payload(make_payload)
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["changed"] is False
    # An unchanged design space must not force a re-validation.
    assert response.json()["view"]["campaignRun"]["status"] == "DesignSpaceValidated"


def test_edit_after_batch_conflicts(client, headers, make_payload) -> None:
    run_id = _create_run(client, headers, make_payload)["campaignRun"]["id"]
    client.post(f"/api/v1/campaign-runs/{run_id}/validate", headers=headers)
    generated = client.post(
        f"/api/v1/campaign-runs/{run_id}/initial-design", headers=headers
    )
    assert generated.status_code == 201

    body = _design_payload(
        make_payload, parameters=_changed_parameters(make_payload)
    )
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


def test_unknown_run_is_not_found(client, headers, make_payload) -> None:
    body = _design_payload(make_payload)
    response = client.put(
        "/api/v1/campaign-runs/does-not-exist/design-space",
        json=body,
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_invalid_body_is_unprocessable(client, headers, make_payload) -> None:
    run_id = _create_run(client, headers, make_payload)["campaignRun"]["id"]
    body = _design_payload(make_payload)
    body["parameters"] = []  # violates min_length=1
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_persistence_failure_rolls_back_everything(
    client, headers, make_payload, monkeypatch
) -> None:
    created = _create_run(client, headers, make_payload)
    run_id = created["campaignRun"]["id"]
    original_head = created["campaignDefinition"]["headRevisionId"]
    original_policy_id = created["campaignRun"]["optimizationPolicy"]["id"]

    def _boom(self, run) -> None:
        raise PersistenceError("simulated mid-transaction failure")

    monkeypatch.setattr(SqliteRepository, "save_run", _boom)

    body = _design_payload(
        make_payload, parameters=_changed_parameters(make_payload)
    )
    response = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert response.status_code == 409

    # The new revision, the head advance, the repin, and the policy swap all
    # rolled back together.
    view = client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert view["campaignDefinition"]["headRevisionId"] == original_head
    assert view["pinnedRevision"]["revisionNumber"] == 1
    assert view["campaignRun"]["optimizationPolicy"]["id"] == original_policy_id

    # And the rolled-back revision consumed no number: a clean retry becomes 2.
    monkeypatch.undo()
    retry = client.put(
        f"/api/v1/campaign-runs/{run_id}/design-space", json=body, headers=headers
    )
    assert retry.status_code == 200
    assert retry.json()["view"]["pinnedRevision"]["revisionNumber"] == 2
