"""Unit tests for :func:`build_effect_preview` (§二/§三 execution preview).

These exercise the dry-run preview directly on a revision — no repository, no
model — proving the structured ``effectPreview`` reflects the *final persisted*
values and honors partial-update semantics (omitted keeps, explicit null clears,
type change surfaces every field, delete lists the entity's real fields).
"""

from __future__ import annotations

import pytest

from backend.agent.contract import (
    AgentObjectivePatch,
    AgentParameterPatch,
    DeleteObjectiveOp,
    DeleteParameterOp,
    SetFixedSumConstraintOp,
    UpdateObjectiveOp,
    UpdateParameterOp,
)
from backend.agent.errors import AgentActionRejectedError
from backend.agent.patch import build_effect_preview
from backend.domain.models import Bounds, ContinuousParameterSpec, OutputSpec


def _fields(preview: dict) -> dict[str, tuple[str | None, str | None]]:
    """Map changedFields to {field: (before, after)} for easy assertions."""
    return {c["field"]: (c["before"], c["after"]) for c in preview["changedFields"]}


@pytest.fixture
def revision(make_revision):
    # resin/hard are Continuous 0–100; add a unit + description on resin so a
    # partial update can be shown to preserve them.
    return make_revision(
        parameters=[
            ContinuousParameterSpec(
                id="resin",
                name="Resin Ratio",
                unit="%",
                description="mix fraction",
                bounds=Bounds(lower=0, upper=100),
            ),
            ContinuousParameterSpec(
                id="hard", name="Hardener Ratio", bounds=Bounds(lower=0, upper=100)
            ),
        ]
    )


def test_update_only_upper_bound_preserves_unit_and_description(revision):
    op = UpdateParameterOp(id="resin", patch=AgentParameterPatch(upper_bound=90))
    preview = build_effect_preview(revision, op)

    assert preview["operation"] == "update"
    assert preview["entityName"] == "Resin Ratio"
    fields = _fields(preview)
    # Only the bound moved; unit/description/name are untouched, so not listed.
    assert fields == {"upperBound": ("100", "90")}


def test_explicit_null_clears_optional_field(revision):
    op = UpdateParameterOp(
        id="resin", patch=AgentParameterPatch.model_validate({"unit": None})
    )
    preview = build_effect_preview(revision, op)

    # A cleared field renders before -> "" (the frontend shows "(empty)").
    assert _fields(preview) == {"unit": ("%", "")}


def test_description_change_is_visible(revision):
    op = UpdateParameterOp(
        id="resin", patch=AgentParameterPatch(description="new note")
    )
    assert _fields(build_effect_preview(revision, op)) == {
        "description": ("mix fraction", "new note")
    }


def test_continuous_to_categorical_shows_every_change(revision):
    op = UpdateParameterOp(
        id="resin",
        patch=AgentParameterPatch.model_validate(
            {"type": "Categorical", "values": ["low", "high"]}
        ),
    )
    fields = _fields(build_effect_preview(revision, op))

    # Type flips, the continuous bounds fall away (absent on the Categorical
    # side, so after is null), and the values list appears.
    assert fields["type"] == ("Continuous", "Categorical")
    assert fields["lowerBound"] == ("0", None)
    assert fields["upperBound"] == ("100", None)
    assert fields["values"] == (None, "low, high")


def test_delete_parameter_previews_full_entity_not_just_id(revision):
    preview = build_effect_preview(revision, DeleteParameterOp(id="resin"))

    assert preview["operation"] == "delete"
    assert preview["entityName"] == "Resin Ratio"
    fields = _fields(preview)
    # Every real field of the deleted parameter is listed (before only).
    assert fields["type"] == ("Continuous", None)
    assert fields["unit"] == ("%", None)
    assert fields["lowerBound"] == ("0", None)
    assert fields["upperBound"] == ("100", None)


def test_delete_objective_previews_full_entity(revision):
    preview = build_effect_preview(revision, DeleteObjectiveOp(id="t1"))

    assert preview["operation"] == "delete"
    assert preview["entityName"] == "Strength"
    assert _fields(preview)["direction"] == ("Maximize", None)


def test_update_objective_direction_change(revision):
    op = UpdateObjectiveOp(id="t1", patch=AgentObjectivePatch(direction="Minimize"))
    preview = build_effect_preview(revision, op)

    assert preview["entityType"] == "objective"
    assert _fields(preview) == {"direction": ("Maximize", "Minimize")}


def test_update_unknown_parameter_is_refused(revision):
    op = UpdateParameterOp(id="nope", patch=AgentParameterPatch(name="X"))
    with pytest.raises(AgentActionRejectedError):
        build_effect_preview(revision, op)


def test_delete_unknown_objective_is_refused(revision):
    with pytest.raises(AgentActionRejectedError):
        build_effect_preview(revision, DeleteObjectiveOp(id="nope"))


def test_change_to_categorical_with_empty_values_is_refused(revision):
    # An empty values list can't build a domain spec; the preview must refuse it
    # rather than emit a bogus change set.
    op = UpdateParameterOp(
        id="resin",
        patch=AgentParameterPatch.model_construct(type="Categorical", values=[]),
    )
    with pytest.raises(AgentActionRejectedError):
        build_effect_preview(revision, op)


def test_fixed_sum_constraint_preview_summarizes(revision):
    op = SetFixedSumConstraintOp(parameter_ids=["resin", "hard"], rhs=100)
    preview = build_effect_preview(revision, op)

    assert preview["entityType"] == "constraint"
    assert preview["operation"] == "set"
    assert _fields(preview)["fixedSum"] == (None, "resin + hard = 100")
