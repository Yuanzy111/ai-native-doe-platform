"""Request bodies for the API (architecture v0.2, §6).

Only the fields a user should supply are accepted. Aggregate-root ids
(campaign definition / revision / run), ``revisionNumber``, timestamps, the
initial run status, ``budgetUsed`` and ``round`` are all assigned by the server
in the router and are intentionally absent here. The *stable* ids of
parameters, outputs, targets and constraints are accepted because they reference
one another, so they must be caller-controlled.

The parameter/output/target/objective/constraint payloads reuse the domain
models directly, so their structural invariants (bounds, discriminated unions,
per-item cross-field rules) are enforced by the same code that guards the
persisted shape, and any violation surfaces as a ``422 VALIDATION_ERROR``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from backend.domain.models import (
    ConstraintSpec,
    Ident,
    ObjectivePolicy,
    OutputSpec,
    ParameterSpec,
    SeedPolicy,
    StrategyConfig,
    TargetSpec,
)


class _RequestBase(BaseModel):
    """Base for request bodies: camelCase aliases, reject unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


class OptimizationPolicyInput(_RequestBase):
    """The user-supplied execution policy (its id is assigned by the server)."""

    backend_name: str = "baybe"
    batch_size: int = Field(ge=1)
    seed_policy: SeedPolicy
    seed_value: int | None = None
    strategy_config: StrategyConfig


class CreateCampaignRunRequest(_RequestBase):
    """The body of ``POST /api/v1/campaign-runs``."""

    name: Ident
    goal: str | None = None
    parameters: list[ParameterSpec] = Field(min_length=1)
    outputs: list[OutputSpec] = Field(min_length=1)
    targets: list[TargetSpec] = Field(min_length=1)
    objective_policy: ObjectivePolicy
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    constraints_confirmed: bool = False
    optimization_policy: OptimizationPolicyInput
    budget_total: int = Field(ge=1)


class SaveDesignSpaceRequest(_RequestBase):
    """The body of ``PUT /api/v1/campaign-runs/{runId}/design-space``.

    Carries only the editable design space and policy; the run, campaign, and
    budget are addressed by the path and never re-supplied here.
    """

    parameters: list[ParameterSpec] = Field(min_length=1)
    outputs: list[OutputSpec] = Field(min_length=1)
    targets: list[TargetSpec] = Field(min_length=1)
    objective_policy: ObjectivePolicy
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    constraints_confirmed: bool = False
    optimization_policy: OptimizationPolicyInput


class PostAgentMessageRequest(_RequestBase):
    """The body of ``POST /api/v1/campaign-runs/{runId}/agent/messages``."""

    message: str = Field(min_length=1, max_length=4000)


__all__ = [
    "CreateCampaignRunRequest",
    "OptimizationPolicyInput",
    "PostAgentMessageRequest",
    "SaveDesignSpaceRequest",
]
