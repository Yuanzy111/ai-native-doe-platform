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
    AgentInvalidActionError,
    AgentNotConfiguredError,
    InvalidAgentOutputError,
    StaleAgentProposalError,
)
from backend.agent.service import AgentService
from backend.api.query import RunQueryService
from backend.application import ApplicationService, ServiceError
from backend.domain.models import AgentProposalStatus, RunStatus

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
_INVALID_BOUNDS = {
    "kind": "designSpacePatch",
    "patch": {
        "op": "addParameter",
        "parameter": {
            "type": "Continuous",
            "name": "Bad",
            "lowerBound": 80,
            "upperBound": 20,
        },
    },
}
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


# §6 — atomic turn: a failure leaves no orphaned message or proposal -----------


def test_invalid_json_leaves_no_orphaned_message(agent, fake_agent_model):
    fake_agent_model.queue("this is not json")
    with pytest.raises(InvalidAgentOutputError):
        agent.post_message(_RUN_ID, _ACTOR, "Hi")
    thread = agent.get_thread(_RUN_ID)
    assert thread["messages"] == []
    assert thread["pendingProposals"] == []


def test_invalid_action_bounds_raise_invalid_action_no_orphans(
    agent, fake_agent_model, seeded_run
):
    fake_agent_model.queue(_turn("Adding it.", _INVALID_BOUNDS))
    with pytest.raises(AgentInvalidActionError):
        agent.post_message(_RUN_ID, _ACTOR, "Add a bad parameter")
    thread = agent.get_thread(_RUN_ID)
    assert thread["messages"] == []
    assert thread["pendingProposals"] == []
    assert _param_count(seeded_run) == 2


def test_illegal_action_leaves_no_orphaned_message(agent, fake_agent_model):
    fake_agent_model.queue(_turn("Updating.", _UPDATE_UNKNOWN))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Update missing param")
    assert agent.get_thread(_RUN_ID)["messages"] == []


# §7 — atomic approval: downstream failure rolls back, marks Failed ------------


def test_approval_failure_rolls_back_and_marks_failed(
    agent, fake_agent_model, seeded_run, monkeypatch, application
):
    fake_agent_model.queue(_turn("Adding it.", _ADD_PARAMETER))
    proposal_id = agent.post_message(_RUN_ID, _ACTOR, "Add temperature")[
        "pendingProposals"
    ][0]["id"]

    def _boom(*_args, **_kwargs):
        raise ServiceError("simulated downstream failure")

    monkeypatch.setattr(application, "save_design_space", _boom)

    with pytest.raises(ServiceError):
        agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)

    # Campaign is untouched and the proposal is Failed, never Pending-with-mutation.
    assert _param_count(seeded_run) == 2
    proposal = seeded_run.get_agent_proposal(proposal_id)
    assert proposal.status is AgentProposalStatus.FAILED
    assert proposal.error


# §4 — state gating at proposal creation ---------------------------------------


def test_generate_rejected_before_validation(agent, fake_agent_model):
    fake_agent_model.queue(_turn("Generating.", _GENERATE))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Generate")


def test_validate_rejected_when_already_validated(
    agent, fake_agent_model, application
):
    application.validate_design_space(_RUN_ID, _ACTOR)
    fake_agent_model.queue(_turn("Validate again.", _VALIDATE))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Validate")


def test_validate_and_generate_rejected_after_batch(
    agent, fake_agent_model, application
):
    application.validate_design_space(_RUN_ID, _ACTOR)
    application.generate_initial_design(_RUN_ID, _ACTOR)

    fake_agent_model.queue(_turn("Validate.", _VALIDATE))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Validate")

    fake_agent_model.queue(_turn("Generate.", _GENERATE))
    with pytest.raises(AgentActionRejectedError):
        agent.post_message(_RUN_ID, _ACTOR, "Generate")


# §5 — approving validate surfaces the real result -----------------------------


def test_approve_validate_returns_passing_result(agent, fake_agent_model):
    fake_agent_model.queue(_turn("Validate please.", _VALIDATE))
    proposal_id = agent.post_message(_RUN_ID, _ACTOR, "Validate")[
        "pendingProposals"
    ][0]["id"]

    result = agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)
    assert result["initialDesign"] is None
    assert result["validationResult"] is not None
    assert result["validationResult"]["ok"] is True


def test_approve_validate_returns_failing_result(
    repo, fake_agent_model, make_run, make_definition, make_revision
):
    # A run whose revision has unconfirmed constraints fails validation but the
    # proposal is still Approved: "approved" is not "validation passed".
    with repo.transaction():
        repo.add_definition(make_definition())
        repo.add_revision(make_revision(constraints_confirmed=False))
        repo.add_run(make_run())
    application = ApplicationService(repo)
    agent = AgentService(
        repo, fake_agent_model, application, RunQueryService(repo)
    )

    fake_agent_model.queue(_turn("Validate please.", _VALIDATE))
    proposal_id = agent.post_message(_RUN_ID, _ACTOR, "Validate")[
        "pendingProposals"
    ][0]["id"]
    result = agent.approve_proposal(_RUN_ID, proposal_id, _ACTOR)

    assert result["proposal"]["status"] == "Approved"
    assert result["validationResult"]["ok"] is False
    assert result["validationResult"]["issues"]
    assert repo.get_run(_RUN_ID).status is RunStatus.DRAFT


# §3 — recommendations context is sourced from persisted data ------------------


def test_context_includes_real_persisted_candidate(
    agent, fake_agent_model, application
):
    application.validate_design_space(_RUN_ID, _ACTOR)
    batch = application.generate_initial_design(_RUN_ID, _ACTOR)

    fake_agent_model.queue(_turn("Here is what the batch shows."))
    agent.post_message(_RUN_ID, _ACTOR, "Explain the recommendations")

    system_prompt = fake_agent_model.calls[-1][0]
    assert batch.candidates[0].id in system_prompt
    # Initial-design candidates carry no model prediction.
    assert "尚无模型预测" in system_prompt
    # The batch id is real, drawn from persistence, not fabricated by the model.
    assert batch.id in system_prompt


# §9 — model history is bounded ------------------------------------------------


def test_model_history_is_capped(agent, fake_agent_model):
    from backend.agent.service import _MAX_HISTORY_MESSAGES

    for i in range(_MAX_HISTORY_MESSAGES + 5):
        fake_agent_model.queue(_turn(f"reply {i}"))
        agent.post_message(_RUN_ID, _ACTOR, f"message {i}")

    last_history = fake_agent_model.calls[-1][1]
    assert len(last_history) <= _MAX_HISTORY_MESSAGES
    # The full transcript is still retained in SQLite.
    assert len(agent.get_thread(_RUN_ID)["messages"]) == 2 * (
        _MAX_HISTORY_MESSAGES + 5
    )
