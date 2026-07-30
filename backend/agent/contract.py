"""The structured output contract for Agent v0 (§二/§三).

Every agent turn is a single :class:`AgentTurn`: a natural-language ``message``
plus at most one ``proposedAction``. The action is a discriminated union that
can only *propose* — never execute — one of three things:

* a **design-space patch** carrying exactly one structured op, or
* a request to **validate** the design space, or
* a request to **generate** the initial design.

The model never supplies entity ids for *new* entities (the backend mints them);
``update``/``delete`` ops carry the existing id, which the patch layer validates
against the current revision. Custom constraint expressions are intentionally not
representable — only the fixed-sum / no-constraint shapes this milestone supports.

Every model is camelCase-aliased and ``extra="forbid"`` so a hallucinated field
or an unrecognized action/op tag fails validation instead of being ignored.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from backend.domain.models import Direction


class _AgentBase(BaseModel):
    """Base for agent contract models: camelCase aliases, reject unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


# Parameter payloads --------------------------------------------------------


class AgentContinuousParameter(_AgentBase):
    """A continuous parameter proposed by the agent (no id: minted by backend)."""

    type: Literal["Continuous"] = "Continuous"
    name: str
    unit: str | None = None
    description: str | None = None
    lower_bound: float
    upper_bound: float


class AgentDiscreteParameter(_AgentBase):
    """A discrete numeric parameter proposed by the agent."""

    type: Literal["Discrete"] = "Discrete"
    name: str
    unit: str | None = None
    description: str | None = None
    values: Annotated[list[float], Field(min_length=1)]


class AgentCategoricalParameter(_AgentBase):
    """A categorical parameter proposed by the agent."""

    type: Literal["Categorical"] = "Categorical"
    name: str
    unit: str | None = None
    description: str | None = None
    values: Annotated[list[str], Field(min_length=1)]


AgentParameterInput = Annotated[
    Union[
        AgentContinuousParameter,
        AgentDiscreteParameter,
        AgentCategoricalParameter,
    ],
    Field(discriminator="type"),
]
"""Discriminated union of parameter payloads, keyed on ``type``."""


class AgentObjectiveInput(_AgentBase):
    """An optimization objective proposed by the agent (one output + direction)."""

    name: str
    direction: Direction
    unit: str | None = None
    description: str | None = None


# Patch ops -----------------------------------------------------------------


class AddParameterOp(_AgentBase):
    """Add a new parameter; the backend mints its id."""

    op: Literal["addParameter"] = "addParameter"
    parameter: AgentParameterInput


class AgentParameterPatch(_AgentBase):
    """A *partial* parameter change: only the fields present are applied.

    Unlike :data:`AgentParameterInput` (a full replacement), every field here is
    optional. A field absent from the payload keeps the parameter's current
    value; ``unit``/``description`` set explicitly to ``null`` clear it. The two
    cases are told apart by :attr:`pydantic.BaseModel.model_fields_set`, so the
    patch layer never confuses "not mentioned" with "cleared". ``type`` may be
    changed (e.g. Continuous → Categorical), in which case the fields the new
    type needs (``values`` / ``lowerBound``+``upperBound``) must be supplied; the
    merged result is validated against the real domain model.
    """

    name: str | None = None
    unit: str | None = None
    description: str | None = None
    type: Literal["Continuous", "Discrete", "Categorical"] | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    values: list[float] | list[str] | None = None


class UpdateParameterOp(_AgentBase):
    """Apply a partial change to an existing parameter, keyed on its current id."""

    op: Literal["updateParameter"] = "updateParameter"
    id: str
    patch: AgentParameterPatch


class DeleteParameterOp(_AgentBase):
    """Delete an existing parameter by id."""

    op: Literal["deleteParameter"] = "deleteParameter"
    id: str


class AddObjectiveOp(_AgentBase):
    """Add a new objective (output + target); the backend mints their ids."""

    op: Literal["addObjective"] = "addObjective"
    objective: AgentObjectiveInput


class AgentObjectivePatch(_AgentBase):
    """A *partial* objective change: only the fields present are applied.

    Every field is optional; an absent field keeps the objective's current
    value, while ``unit``/``description`` set explicitly to ``null`` clear it
    (told apart via :attr:`pydantic.BaseModel.model_fields_set`).
    """

    name: str | None = None
    direction: Direction | None = None
    unit: str | None = None
    description: str | None = None


class UpdateObjectiveOp(_AgentBase):
    """Apply a partial change to an existing objective, keyed on its current id."""

    op: Literal["updateObjective"] = "updateObjective"
    id: str
    patch: AgentObjectivePatch


class DeleteObjectiveOp(_AgentBase):
    """Delete an existing objective by id."""

    op: Literal["deleteObjective"] = "deleteObjective"
    id: str


class SetNoConstraintOp(_AgentBase):
    """Declare there is no fixed-sum constraint (and confirm the choice)."""

    op: Literal["setNoConstraint"] = "setNoConstraint"


class SetFixedSumConstraintOp(_AgentBase):
    """Declare a fixed-sum constraint over parameters summing to ``rhs``.

    When ``parameterIds`` is omitted the patch layer picks the resin/hardener
    pair (or the first two parameters), mirroring the manual control.
    """

    op: Literal["setFixedSumConstraint"] = "setFixedSumConstraint"
    parameter_ids: list[str] | None = None
    rhs: float = 100.0


PatchOp = Annotated[
    Union[
        AddParameterOp,
        UpdateParameterOp,
        DeleteParameterOp,
        AddObjectiveOp,
        UpdateObjectiveOp,
        DeleteObjectiveOp,
        SetNoConstraintOp,
        SetFixedSumConstraintOp,
    ],
    Field(discriminator="op"),
]
"""Discriminated union of design-space patch ops, keyed on ``op``."""


# Actions -------------------------------------------------------------------


class DesignSpacePatchAction(_AgentBase):
    """Propose exactly one structured change to the design space."""

    kind: Literal["designSpacePatch"] = "designSpacePatch"
    patch: PatchOp


class ValidateDesignSpaceAction(_AgentBase):
    """Propose running the deterministic design-space validation."""

    kind: Literal["validateDesignSpace"] = "validateDesignSpace"


class GenerateInitialDesignAction(_AgentBase):
    """Propose generating the first-round design via the real optimizer."""

    kind: Literal["generateInitialDesign"] = "generateInitialDesign"


AgentAction = Annotated[
    Union[
        DesignSpacePatchAction,
        ValidateDesignSpaceAction,
        GenerateInitialDesignAction,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union of proposable actions, keyed on ``kind``."""


# Effect preview ------------------------------------------------------------


class ChangedField(_AgentBase):
    """One field's before/after in an :class:`EffectPreview`.

    ``before`` is ``null`` for an add (nothing existed) and ``after`` is ``null``
    for a delete. Both are rendered strings — never raw model values — so the
    frontend renders the preview verbatim without re-deriving business meaning.
    An empty string is shown by the UI as ``(empty)``.
    """

    field: str
    before: str | None
    after: str | None


class EffectPreview(_AgentBase):
    """The structured, backend-computed effect a mutation proposal will have.

    Built by dry-running the patch against the proposal's ``baseRevisionId`` so
    it reflects exactly what approval will persist (final merged values, not the
    raw op). The frontend renders this directly; it never recomputes a diff from
    on-screen state.
    """

    entity_type: Literal["parameter", "objective", "constraint"]
    entity_id: str | None = None
    operation: Literal["add", "update", "delete", "set"]
    entity_name: str | None = None
    changed_fields: list[ChangedField] = Field(default_factory=list)


class AgentTurn(_AgentBase):
    """One assistant turn: a non-empty message plus at most one proposed action."""

    message: Annotated[str, Field(min_length=1)]
    proposed_action: AgentAction | None = None

    @field_validator("message")
    @classmethod
    def _reject_blank_message(cls, value: str) -> str:
        """Reject a whitespace-only message (``min_length`` alone allows ``" "``)."""
        if not value.strip():
            raise ValueError("The assistant message must not be blank.")
        return value


__all__ = [
    "AgentTurn",
    "AgentAction",
    "DesignSpacePatchAction",
    "ValidateDesignSpaceAction",
    "GenerateInitialDesignAction",
    "PatchOp",
    "AddParameterOp",
    "UpdateParameterOp",
    "DeleteParameterOp",
    "AddObjectiveOp",
    "UpdateObjectiveOp",
    "DeleteObjectiveOp",
    "SetNoConstraintOp",
    "SetFixedSumConstraintOp",
    "AgentParameterInput",
    "AgentContinuousParameter",
    "AgentDiscreteParameter",
    "AgentCategoricalParameter",
    "AgentObjectiveInput",
    "AgentParameterPatch",
    "AgentObjectivePatch",
    "ChangedField",
    "EffectPreview",
]
