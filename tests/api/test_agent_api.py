"""End-to-end HTTP tests for the agent API (Agent v0, §四).

These drive the real Starlette ``TestClient`` against a request-scoped SQLite
file and the real vendored BayBE adapter, with only the LLM boundary faked: a
:class:`~tests.conftest.FakeAgentModel` is injected via ``create_app`` so no
network or real model is ever touched. The suite covers the propose-then-approve
contract at the wire level — the unconfigured 503, a full send→approve flow that
mutates the campaign only on approval, and the bad-output 502.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from backend.adapters.baybe import BayBEAdapter
from backend.agent.model import OpenAICompatibleAgentModel
from backend.api import create_app

_ADD_PARAMETER = {
    "kind": "designSpacePatch",
    "patch": {
        "op": "addParameter",
        "parameter": {
            "type": "Continuous",
            "name": "Temperature",
            "lowerBound": 20,
            "upperBound": 80,
        },
    },
}
_GENERATE = {"kind": "generateInitialDesign"}
_VALIDATE = {"kind": "validateDesignSpace"}
_SECRET = "sk-super-secret-key"


def _turn(message: str, action: dict | None = None) -> str:
    body: dict = {"message": message}
    if action is not None:
        body["proposedAction"] = action
    return json.dumps(body)


@pytest.fixture
def agent_client(tmp_path, fake_agent_model) -> TestClient:
    """A TestClient over an app wired to real BayBE and the fake agent model."""
    app = create_app(
        db_path=str(tmp_path / "agent-api-test.db"),
        adapter=BayBEAdapter(),
        agent_model=fake_agent_model,
    )
    with TestClient(app) as test_client:
        yield test_client


def _create_run(client, headers, make_payload) -> str:
    created = client.post("/api/v1/campaign-runs", json=make_payload(), headers=headers)
    assert created.status_code == 201
    return created.json()["campaignRun"]["id"]


def test_unconfigured_model_returns_503(tmp_path, headers, make_payload) -> None:
    app = create_app(db_path=str(tmp_path / "no-agent.db"), adapter=BayBEAdapter())
    with TestClient(app) as client:
        run_id = _create_run(client, headers, make_payload)
        response = client.post(
            f"/api/v1/campaign-runs/{run_id}/agent/messages",
            json={"message": "Hi"},
            headers=headers,
        )
    assert response.status_code == 503
    assert response.json()["code"] == "AGENT_NOT_CONFIGURED"


def test_send_then_approve_applies_patch(
    agent_client, fake_agent_model, headers, make_payload
) -> None:
    run_id = _create_run(agent_client, headers, make_payload)

    fake_agent_model.queue(_turn("Adding a parameter.", _ADD_PARAMETER))
    sent = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "Add temperature"},
        headers=headers,
    )
    assert sent.status_code == 200
    body = sent.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert len(body["pendingProposals"]) == 1
    proposal_id = body["pendingProposals"][0]["id"]

    # Sending never mutates the campaign: the run is still a Draft.
    view = agent_client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert view["campaignRun"]["status"] == "Draft"
    assert len(view["pinnedRevision"]["parameters"]) == 2

    approved = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/proposals/{proposal_id}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    approve_body = approved.json()
    assert approve_body["proposal"]["status"] == "Approved"
    assert approve_body["initialDesign"] is None
    assert len(approve_body["view"]["pinnedRevision"]["parameters"]) == 3

    # The thread now has no pending proposals.
    thread = agent_client.get(
        f"/api/v1/campaign-runs/{run_id}/agent/thread"
    ).json()
    assert thread["pendingProposals"] == []


def test_generate_runs_through_application_service(
    agent_client, fake_agent_model, headers, make_payload
) -> None:
    run_id = _create_run(agent_client, headers, make_payload)
    validated = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/validate", headers=headers
    )
    assert validated.status_code == 200

    fake_agent_model.queue(_turn("Generating the initial design.", _GENERATE))
    sent = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "Generate"},
        headers=headers,
    )
    proposal_id = sent.json()["pendingProposals"][0]["id"]

    approved = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/proposals/{proposal_id}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["initialDesign"] is not None
    assert body["initialDesign"]["recommendationBatch"]["candidates"]
    assert body["view"]["campaignRun"]["status"] == "RecommendationsPending"


def test_bad_model_output_returns_502(
    agent_client, fake_agent_model, headers, make_payload
) -> None:
    run_id = _create_run(agent_client, headers, make_payload)
    fake_agent_model.queue("this is not json")
    response = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "Hi"},
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["code"] == "AGENT_INVALID_OUTPUT"


def test_reject_does_not_mutate(
    agent_client, fake_agent_model, headers, make_payload
) -> None:
    run_id = _create_run(agent_client, headers, make_payload)
    fake_agent_model.queue(_turn("Adding a parameter.", _ADD_PARAMETER))
    sent = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "Add temperature"},
        headers=headers,
    )
    proposal_id = sent.json()["pendingProposals"][0]["id"]

    rejected = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/proposals/{proposal_id}/reject",
        headers=headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["pendingProposals"] == []

    view = agent_client.get(f"/api/v1/campaign-runs/{run_id}").json()
    assert len(view["pinnedRevision"]["parameters"]) == 2


# §8 — configured env but the optional 'openai' extra is absent ----------------


def test_missing_sdk_returns_503_without_leaking_key(
    tmp_path, monkeypatch, headers, make_payload
) -> None:
    # An app configured with a real OpenAI-compatible model still boots when the
    # SDK is absent; the failure surfaces only on the first message, as a 503
    # with no API key or traceback in the body.
    monkeypatch.setitem(sys.modules, "openai", None)
    model = OpenAICompatibleAgentModel(
        base_url="https://example.test/v1", api_key=_SECRET, model="gpt-x"
    )
    app = create_app(
        db_path=str(tmp_path / "missing-sdk.db"),
        adapter=BayBEAdapter(),
        agent_model=model,
    )
    with TestClient(app) as client:
        run_id = _create_run(client, headers, make_payload)
        response = client.post(
            f"/api/v1/campaign-runs/{run_id}/agent/messages",
            json={"message": "Hi"},
            headers=headers,
        )
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "AGENT_DEPENDENCY_MISSING"
    # The key never leaks, and no traceback frames surface in the wire response.
    serialized = json.dumps(body)
    assert _SECRET not in serialized
    assert "Traceback" not in serialized


# §9 — message length bounds ---------------------------------------------------


def test_empty_message_rejected(agent_client, headers, make_payload) -> None:
    run_id = _create_run(agent_client, headers, make_payload)
    response = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": ""},
        headers=headers,
    )
    assert response.status_code == 422


def test_overlong_message_rejected(agent_client, headers, make_payload) -> None:
    run_id = _create_run(agent_client, headers, make_payload)
    response = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "x" * 4001},
        headers=headers,
    )
    assert response.status_code == 422


# §5 — approving a validate proposal returns the real validation result --------


def test_approve_validate_returns_validation_result(
    agent_client, fake_agent_model, headers, make_payload
) -> None:
    run_id = _create_run(agent_client, headers, make_payload)

    fake_agent_model.queue(_turn("Validate please.", _VALIDATE))
    sent = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/messages",
        json={"message": "Validate"},
        headers=headers,
    )
    proposal_id = sent.json()["pendingProposals"][0]["id"]

    approved = agent_client.post(
        f"/api/v1/campaign-runs/{run_id}/agent/proposals/{proposal_id}/approve",
        headers=headers,
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["proposal"]["status"] == "Approved"
    assert body["initialDesign"] is None
    assert body["validationResult"] is not None
    assert body["validationResult"]["ok"] is True
