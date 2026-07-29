"""Unit tests for :class:`AgentService` (Agent v0, §三/§四).

These drive the service directly over an in-memory repository and the
:class:`FakeAgentModel`, so no network or real model is ever touched. The real
:class:`ApplicationService` (wired to a :class:`FakeOptimizerAdapter`) remains
the sole authority for state changes, so an approved generate still runs the
deterministic pipeline end to end.
"""

from __future__ import annotations

import json

import pytest

from backend.agent.errors import (
    AgentActionRejectedError,
    AgentNotConfiguredError,
    InvalidAgentOutputError,
    StaleAgentProposalError,
)
from backend.agent.service import AgentService
from backend.api.query import RunQueryService
from backend.application import ApplicationService
from backend.domain.models import RunStatus

_RUN_ID = "run-1"
_ACTOR = "user-1"

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
_VALIDATE = {"kind": "validateDesignSpace"}
_GENERATE = {"kind": "generateInitialDesign"}
_UPDATE_UNKNOWN = {
    "kind": "designSpacePatch",
    "patch": {
        "op": "updateParameter",
        "id": "does-not-exist",
        "parameter": {"type": "Continuous", "name": "X", "lowerBound": 0, "upperBound": 1},
    },
}


def _turn(message: str, action: dict | None = None) -> str:
    body: dict = {"message": message}
    if action is not None:
        body["proposedAction"] = action
    return json.dumps(body)


@pytest.fixture
def application(seeded_run, fake_adapter):
    """The real application service over the seeded repo and fake adapter."""
    return ApplicationService(seeded_run, adapter=fake_adapter)


@pytest.fixture
def agent(seeded_run, fake_agent_model, application):
    """An :class:`AgentService` sharing the seeded repo with ``application``."""
    return AgentService(
        seeded_run,
        fake_agent_model,
        application,
        RunQueryService(seeded_run),
    )


def _param_count(repo) -> int:
    run = repo.get_run(_RUN_ID)
    return len(repo.get_revision(run.definition_revision_id).parameters)


def test_post_message_records_conversation_and_restores(
    agent, fake_agent_model, seeded_run
):
    fake_agent_model.queue(_turn("Hello, how can I help?"))
    result = agent.post_message(_RUN_ID, _ACTOR, "Hi")

    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    assert result["messages"][0]["content"] == "Hi"
    assert result["pendingProposals"] == []

    # A fresh service over the same repo restores the same thread (refresh view).
    restored = AgentService(
        seeded_run, fake_agent_model, None, RunQueryService(seeded_run)  # type: ignore[arg-type]
    ).get_thread(_RUN_ID)
    assert [m["content"] for m in restored["messages"]] == ["Hi", "Hello, how can I help?"]


def test_invalid_json_raises_invalid_output(agent, fake_agent_model):
    fake_agent_model.queue("this is not json")
    with pytest.raises(InvalidAgentOutputError):
        agent.post_message(_RUN_ID, _ACTOR, "Hi")


def test_invalid_turn_shape_raises_invalid_output(agent, fake_agent_model):
    fake_agent_model.queue(json.dumps({"proposedAction": _VALIDATE}))  # no message
    with pytest.raises(InvalidAgentOutputError):
        agent.post_message(_RUN_ID, _ACTOR, "Hi")


def test_proposal_does_not_mutate_campaign(agent, fake_agent_model, seeded_run):
    fake_agent_model.queue(_turn("I propose adding a parameter.", _ADD_PARAMETER))
    result = agent.post_message(_RUN_ID, _ACTOR, "Add temperature")

    assert len(result["pendingProposals"]) == 1
    # Sending a message never mutates the campaign.
    assert _param_count(seeded_run) == 2
    assert seeded_run.get_run(_RUN_ID).status is RunStatus.DRAFT


def test_approve_applies_patch(agent, fake_agent_model, seeded_run):
    fake_agent_model.queue(_turn("Adding it.", _ADD_PARAMETER))
    staged = agent.post_message(_RUN_ID, _ACTOR, "Add temperature")
    proposal_id = staged["pendingProposals"][0]["id"]

    result = agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)

    assert result["proposal"]["status"] == "Approved"
    assert _param_count(seeded_run) == 3
    assert agent.get_thread(_RUN_ID)["pendingProposals"] == []


def test_reject_does_not_mutate(agent, fake_agent_model, seeded_run):
    fake_agent_model.queue(_turn("Adding it.", _ADD_PARAMETER))
    staged = agent.post_message(_RUN_ID, _ACTOR, "Add temperature")
    proposal_id = staged["pendingProposals"][0]["id"]

    agent.reject_proposal(_RUN_ID, proposal_id, _ACTOR)

    assert _param_count(seeded_run) == 2
    assert agent.get_thread(_RUN_ID)["pendingProposals"] == []


def test_stale_proposal_rejected(agent, fake_agent_model):
    fake_agent_model.queue(_turn("First change.", _ADD_PARAMETER))
    first = agent.post_message(_RUN_ID, _ACTOR, "Add A")["pendingProposals"][0]["id"]
    fake_agent_model.queue(_turn("Second change.", _ADD_PARAMETER))
    pending = agent.post_message(_RUN_ID, _ACTOR, "Add B")["pendingProposals"]
    second = next(p["id"] for p in pending if p["id"] != first)

    # Approving the first mints a new revision; the second is now pinned to a
    # revision that no longer matches the run.
    agent.approve_proposal(_RUN_ID, first, _ACTOR)
    with pytest.raises(StaleAgentProposalError):
        agent.approve_proposal(_RUN_ID, second, _ACTOR)


def test_validate_requires_approval(agent, fake_agent_model, seeded_run):
    fake_agent_model.queue(_turn("Shall I validate?", _VALIDATE))
    staged = agent.post_message(_RUN_ID, _ACTOR, "Validate please")
    # Not applied on send.
    assert seeded_run.get_run(_RUN_ID).status is RunStatus.DRAFT

    proposal_id = staged["pendingProposals"][0]["id"]
    agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)
    assert seeded_run.get_run(_RUN_ID).status is RunStatus.DESIGN_SPACE_VALIDATED


def test_generate_runs_through_application_service(
    agent, fake_agent_model, application, seeded_run
):
    application.validate_design_space(_RUN_ID, _ACTOR)

    fake_agent_model.queue(_turn("Generating the initial design.", _GENERATE))
    staged = agent.post_message(_RUN_ID, _ACTOR, "Generate")
    # Still not generated until approved.
    assert seeded_run.get_run(_RUN_ID).status is RunStatus.DESIGN_SPACE_VALIDATED

    proposal_id = staged["pendingProposals"][0]["id"]
    result = agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)

    assert result["initialDesign"] is not None
    assert result["initialDesign"]["recommendationBatch"]["candidates"]
    assert seeded_run.get_run(_RUN_ID).status is RunStatus.RECOMMENDATIONS_PENDING


def test_unknown_action_id_rejected(agent, fake_agent_model):
    fake_agent_model.queue(_turn("Updating.", _UPDATE_UNKNOWN))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Update missing param")


def test_frozen_refuses_modification(agent, fake_agent_model, application):
    application.validate_design_space(_RUN_ID, _ACTOR)
    application.generate_initial_design(_RUN_ID, _ACTOR)

    fake_agent_model.queue(_turn("Adding a parameter.", _ADD_PARAMETER))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Add temperature")


def test_unconfigured_model_raises(seeded_run):
    agent = AgentService(
        seeded_run, None, ApplicationService(seeded_run), RunQueryService(seeded_run)
    )
    with pytest.raises(AgentNotConfiguredError):
        agent.post_message(_RUN_ID, _ACTOR, "Hi")
