"""Tests for the CampaignRun lifecycle state machine (§3.2)."""

import pytest

from backend.domain.models import RunStatus
from backend.domain.validation import (
    RunEvent,
    StateTransitionError,
    can_transition,
    next_status,
)

_LEGAL = [
    (RunStatus.DRAFT, RunEvent.VALIDATE_DEFINITION_PASS, RunStatus.DESIGN_SPACE_VALIDATED),
    (RunStatus.DRAFT, RunEvent.VALIDATE_DEFINITION_FAIL, RunStatus.DRAFT),
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.EDIT_DEFINITION, RunStatus.DRAFT),
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.GENERATE_INITIAL_DESIGN, RunStatus.RECOMMENDATIONS_PENDING),
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.ARCHIVE, RunStatus.ARCHIVED),
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.ALL_RUNS_TERMINAL, RunStatus.AWAITING_MEASUREMENTS),
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.ABORT_ROUND, RunStatus.ROUND_CLOSED),
    (RunStatus.AWAITING_MEASUREMENTS, RunEvent.CLOSE_ROUND, RunStatus.ROUND_CLOSED),
    (RunStatus.AWAITING_MEASUREMENTS, RunEvent.ABORT_ROUND, RunStatus.ROUND_CLOSED),
    (RunStatus.ROUND_CLOSED, RunEvent.RECOMMEND, RunStatus.RECOMMENDATIONS_PENDING),
    (RunStatus.ROUND_CLOSED, RunEvent.MARK_COMPLETED, RunStatus.COMPLETED),
    (RunStatus.ROUND_CLOSED, RunEvent.ARCHIVE, RunStatus.ARCHIVED),
    (RunStatus.COMPLETED, RunEvent.REOPEN, RunStatus.ROUND_CLOSED),
    (RunStatus.COMPLETED, RunEvent.ARCHIVE, RunStatus.ARCHIVED),
]

_ILLEGAL = [
    (RunStatus.DRAFT, RunEvent.RECOMMEND),
    (RunStatus.DRAFT, RunEvent.GENERATE_INITIAL_DESIGN),
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.RECOMMEND),
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.RECOMMEND),
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.CLOSE_ROUND),
    (RunStatus.AWAITING_MEASUREMENTS, RunEvent.RECOMMEND),
    (RunStatus.ROUND_CLOSED, RunEvent.GENERATE_INITIAL_DESIGN),
    (RunStatus.COMPLETED, RunEvent.RECOMMEND),
    (RunStatus.ARCHIVED, RunEvent.RECOMMEND),
    (RunStatus.ARCHIVED, RunEvent.ARCHIVE),
]


@pytest.mark.parametrize(("current", "event", "expected"), _LEGAL)
def test_legal_transition_yields_expected_status(current, event, expected):
    assert can_transition(current, event) is True
    assert next_status(current, event) is expected


@pytest.mark.parametrize(("current", "event"), _ILLEGAL)
def test_illegal_transition_is_reported_and_raises(current, event):
    assert can_transition(current, event) is False
    with pytest.raises(StateTransitionError):
        next_status(current, event)


def test_archived_is_terminal():
    for event in RunEvent:
        assert can_transition(RunStatus.ARCHIVED, event) is False


def test_recommend_only_allowed_from_round_closed():
    allowed = [s for s in RunStatus if can_transition(s, RunEvent.RECOMMEND)]
    assert allowed == [RunStatus.ROUND_CLOSED]
