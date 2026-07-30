"""Domain models for the optimization platform (architecture v0.2, §2).

Pydantic v2 models mirroring the entities and discriminated unions defined in
``docs/architecture-v0.2.md``. Python field names are ``snake_case`` and
serialize to the ``camelCase`` JSON shown in §8 through an alias generator, so
the persisted representation matches the architecture document exactly.

Only per-instance structural invariants are enforced here (field bounds,
discriminated unions, per-item cross-field rules, within-array id uniqueness,
conditionally required fields). Cross-entity semantic checks that produce
user-facing issues (referential integrity, ``constraintsConfirmed`` gating,
Desirability cutoff legality, objective/target consistency, constraint
executability) live in :mod:`backend.domain.validation`.

Hardening (see the backend hardening pass):

* Every collection on a frozen value object is stored as an immutable
  sequence/mapping (:class:`_FrozenList` / :class:`_FrozenDict`), so neither the
  attribute nor its contents can be mutated in place.
* ``model_copy(update=...)`` re-validates, so it cannot be used to bypass the
  invariants below.
* Timestamps are timezone-aware datetimes; numeric fields reject ``NaN``/``Inf``
  and never silently coerce ``bool``; ids/names are stripped and must be
  non-blank.
"""

from typing import Annotated, Any, Literal, Union

from enum import StrEnum

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


# Immutable collections -----------------------------------------------------


class _FrozenList(list):
    """A ``list`` subclass that rejects every in-place mutation.

    Equality still delegates to ``list`` so ``_FrozenList([1]) == [1]`` and JSON
    serialization is unchanged; only the mutating methods are disabled.
    """

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("This sequence is immutable and cannot be modified.")

    append = extend = insert = remove = pop = clear = sort = reverse = _immutable
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable


class _FrozenDict(dict):
    """A ``dict`` subclass that rejects every in-place mutation."""

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("This mapping is immutable and cannot be modified.")

    __setitem__ = __delitem__ = clear = pop = popitem = update = setdefault = _immutable
    __ior__ = _immutable


def _freeze_list(value: object) -> object:
    """Wrap a plain ``list`` in a :class:`_FrozenList` (pass others through)."""
    if isinstance(value, list) and not isinstance(value, _FrozenList):
        return _FrozenList(value)
    return value


def _freeze_dict(value: object) -> object:
    """Wrap a plain ``dict`` in a :class:`_FrozenDict` (pass others through)."""
    if isinstance(value, dict) and not isinstance(value, _FrozenDict):
        return _FrozenDict(value)
    return value


def _reject_bool(value: object) -> object:
    """Reject ``bool`` so ``True``/``False`` never become ``1.0``/``0.0``."""
    if isinstance(value, bool):
        raise ValueError("A boolean is not a valid numeric value.")
    return value


def _strip_non_blank(value: object) -> object:
    """Strip surrounding whitespace and reject blank strings."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank.")
        return stripped
    return value


# Reusable field types ------------------------------------------------------

Ident = Annotated[str, BeforeValidator(_strip_non_blank)]
"""A stripped, non-blank identifier or name."""

Finite = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]
"""A finite real number (no ``NaN``/``Inf``, and ``bool`` is rejected)."""

PositiveFinite = Annotated[
    float, BeforeValidator(_reject_bool), Field(gt=0, allow_inf_nan=False)
]
"""A finite, strictly positive real number."""

_FrozenFloatList = Annotated[list[Finite], AfterValidator(_freeze_list)]
_FrozenIdentList = Annotated[list[Ident], AfterValidator(_freeze_list)]


class _Base(BaseModel):
    """Base for mutable domain models: camelCase aliases, no extra fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    def model_copy(self, *, update: dict[str, Any] | None = None, deep: bool = False):
        """Copy the model, re-validating when ``update`` is supplied.

        Pydantic's default ``model_copy`` assigns ``update`` without validation,
        which would let a caller bypass the invariants declared here. Routing
        the update through :meth:`model_validate` re-runs every validator.
        """
        if not update:
            return super().model_copy(deep=deep)
        data = self.model_dump(mode="python", by_alias=False)
        data.update(update)
        return type(self).model_validate(data)


class _FrozenBase(_Base):
    """Base for immutable value objects: same aliasing plus ``frozen=True``."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


# ParameterSpec (§2.4) ------------------------------------------------------


class Bounds(_FrozenBase):
    """Numeric bounds for a continuous parameter."""

    lower: Finite
    """The inclusive lower bound."""

    upper: Finite
    """The inclusive upper bound."""

    stepsize: PositiveFinite | None = None
    """Optional discretization step; must be positive when given."""


class _ParameterBase(_FrozenBase):
    """Shared fields of every parameter specification."""

    id: Ident
    """The stable identifier used by constraints and value maps."""

    name: Ident
    """The human-readable parameter name."""

    unit: str | None = None
    """The optional physical unit."""

    description: str | None = None
    """The optional free-text description."""


class ContinuousParameterSpec(_ParameterBase):
    """A parameter ranging over a continuous interval."""

    type: Literal["Continuous"] = "Continuous"
    """The discriminator tag."""

    bounds: Bounds
    """The lower/upper interval; ``lower`` must be strictly below ``upper``."""

    @model_validator(mode="after")
    def _validate_bounds(self) -> "ContinuousParameterSpec":
        """Reject empty or inverted intervals."""
        if self.bounds.lower >= self.bounds.upper:
            raise ValueError(
                f"Continuous parameter {self.name!r} requires lower < upper, "
                f"got lower={self.bounds.lower}, upper={self.bounds.upper}."
            )
        return self


class DiscreteParameterSpec(_ParameterBase):
    """A parameter drawn from a finite set of numeric levels."""

    type: Literal["Discrete"] = "Discrete"
    """The discriminator tag."""

    values: Annotated[list[Finite], Field(min_length=1)]
    """The allowed numeric levels, deduplicated and sorted ascending."""

    @field_validator("values")
    @classmethod
    def _dedup_sort(cls, value: list[float]) -> _FrozenList:
        """Deduplicate and sort the numeric levels (documented normalization)."""
        return _FrozenList(sorted(set(value)))


class CategoricalParameterSpec(_ParameterBase):
    """A parameter drawn from a finite set of labels."""

    type: Literal["Categorical"] = "Categorical"
    """The discriminator tag."""

    values: Annotated[list[str], Field(min_length=1)]
    """The allowed labels: non-blank, deduplicated, case-sensitive, ordered."""

    @field_validator("values")
    @classmethod
    def _dedup_preserve_order(cls, value: list[str]) -> _FrozenList:
        """Reject blank labels and drop case-sensitive duplicates in order."""
        seen: list[str] = []
        for label in value:
            if not label.strip():
                raise ValueError("Categorical values must be non-blank.")
            if label not in seen:
                seen.append(label)
        return _FrozenList(seen)


ParameterSpec = Annotated[
    Union[ContinuousParameterSpec, DiscreteParameterSpec, CategoricalParameterSpec],
    Field(discriminator="type"),
]
"""Discriminated union of parameter specifications, keyed on ``type``."""


# OutputSpec (§2.5) ---------------------------------------------------------


class OutputSpec(_FrozenBase):
    """A measurable quantity, carrying no optimization direction."""

    id: Ident
    """The stable identifier referenced by targets and measurements."""

    name: Ident
    """The human-readable output name."""

    unit: str | None = None
    """The optional physical unit."""

    description: str | None = None
    """The optional free-text description."""


# TargetSpec (§2.6) ---------------------------------------------------------


class Direction(StrEnum):
    """The optimization direction of a single target."""

    MAXIMIZE = "Maximize"
    MINIMIZE = "Minimize"


class TargetSpec(_FrozenBase):
    """The optimization direction attached to exactly one output."""

    id: Ident
    """The stable identifier referenced by the objective policy."""

    output_id: Ident
    """The referenced :class:`OutputSpec` id."""

    direction: Direction
    """The optimization direction (``Target``/``CloseToTarget`` are v1+)."""

    target_value: Finite | None = None
    """Reserved for ``direction='Target'`` in v1+; must be ``null`` in the MVP."""

    @model_validator(mode="after")
    def _validate_mvp(self) -> "TargetSpec":
        """The MVP only supports Maximize/Minimize; ``targetValue`` must be null."""
        if self.target_value is not None:
            raise ValueError(
                "targetValue must be null in the MVP (Target/CloseToTarget are v1+)."
            )
        return self


# ObjectivePolicy (§2.7) ----------------------------------------------------


class WeightingMode(StrEnum):
    """Whether Desirability weights are explicit or forced equal."""

    EXPLICIT = "explicit"
    EQUAL = "equal"


class Scalarizer(StrEnum):
    """The Desirability scalarization operator."""

    MEAN = "MEAN"
    GEOM_MEAN = "GEOM_MEAN"


class Cutoffs(_FrozenBase):
    """Explicit scaling cutoffs for a ``NormalizedRamp`` transformation.

    ``lower``/``upper`` legality (``lower < upper``) is intentionally *not*
    enforced here; per §2.7 it is reported by ``validate_definition`` as a
    blocking issue rather than raised at construction.
    """

    lower: Finite
    """The value mapped to one end of the ramp."""

    upper: Finite
    """The value mapped to the other end of the ramp."""


class DesirabilityEntry(_FrozenBase):
    """One target's contribution to a Desirability objective."""

    target_id: Ident
    """The referenced :class:`TargetSpec` id."""

    transformation: Literal["NormalizedRamp"] = "NormalizedRamp"
    """The MVP's only transformation (Target/Triangular/Bell are v1)."""

    cutoffs: Cutoffs
    """The explicit scaling cutoffs; no runtime guessing is permitted."""

    weight: PositiveFinite
    """The relative weight (finite, strictly positive); forced equal when
    ``weightingMode='equal'``."""


class SingleObjectivePolicy(_FrozenBase):
    """A single-target objective."""

    kind: Literal["Single"] = "Single"
    """The discriminator tag."""

    target_id: Ident
    """The single referenced target; requires ``len(targets) == 1``."""


class DesirabilityObjectivePolicy(_FrozenBase):
    """A scalarized multi-target Desirability objective."""

    kind: Literal["Desirability"] = "Desirability"
    """The discriminator tag."""

    entries: Annotated[list[DesirabilityEntry], AfterValidator(_freeze_list), Field(min_length=1)]
    """One entry per target; must cover every target one-to-one."""

    weighting_mode: WeightingMode
    """Whether weights are explicit or forced equal by the validator."""

    scalarizer: Scalarizer = Scalarizer.GEOM_MEAN
    """The scalarization operator (defaults to ``GEOM_MEAN`` like BayBE)."""


class ParetoObjectivePolicy(_FrozenBase):
    """A multi-target Pareto objective (no scalarization)."""

    kind: Literal["Pareto"] = "Pareto"
    """The discriminator tag."""

    target_ids: Annotated[list[Ident], AfterValidator(_freeze_list), Field(min_length=2)]
    """At least two distinct referenced targets; produces a Pareto frontier."""

    @model_validator(mode="after")
    def _validate_unique(self) -> "ParetoObjectivePolicy":
        """Reject duplicate target ids (coverage is checked by the validator)."""
        if len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("Pareto targetIds must be unique.")
        return self


ObjectivePolicy = Annotated[
    Union[SingleObjectivePolicy, DesirabilityObjectivePolicy, ParetoObjectivePolicy],
    Field(discriminator="kind"),
]
"""Discriminated union of objective policies, keyed on ``kind``."""


# ConstraintSpec (§2.8) -----------------------------------------------------


class _ConstraintBase(_FrozenBase):
    """Shared fields of every constraint specification."""

    id: Ident
    """The stable identifier."""

    resolved_at: AwareDatetime | None = None
    """The optional timezone-aware timestamp at which the constraint resolved."""


def _validate_parameter_ids_unique(constraint: "_ConstraintBase") -> None:
    """Raise when a constraint lists the same parameter id twice."""
    ids = constraint.parameter_ids  # type: ignore[attr-defined]
    if len(set(ids)) != len(ids):
        raise ValueError(
            f"Constraint {constraint.id!r}: parameterIds must be unique."
        )


class LinearEqualityConstraintSpec(_ConstraintBase):
    """A linear equality over parameter values."""

    kind: Literal["LinearEquality"] = "LinearEquality"
    """The discriminator tag."""

    parameter_ids: Annotated[list[Ident], AfterValidator(_freeze_list), Field(min_length=2)]
    """The referenced parameter ids (at least two, unique)."""

    coefficients: _FrozenFloatList
    """The per-parameter coefficients; finite, not all zero, matching length."""

    rhs: Finite
    """The right-hand side of ``Σ coefficients[i] * value[i] = rhs``."""

    @model_validator(mode="after")
    def _validate_structure(self) -> "LinearEqualityConstraintSpec":
        """Reject length mismatch, duplicate ids, and all-zero coefficients."""
        if len(self.coefficients) != len(self.parameter_ids):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients length "
                f"{len(self.coefficients)} must equal parameterIds length "
                f"{len(self.parameter_ids)}."
            )
        _validate_parameter_ids_unique(self)
        if all(c == 0 for c in self.coefficients):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients must not be all zero."
            )
        return self


class LinearInequalityConstraintSpec(_ConstraintBase):
    """A linear inequality over parameter values."""

    kind: Literal["LinearInequality"] = "LinearInequality"
    """The discriminator tag."""

    parameter_ids: Annotated[list[Ident], AfterValidator(_freeze_list), Field(min_length=2)]
    """The referenced parameter ids (at least two, unique)."""

    coefficients: _FrozenFloatList
    """The per-parameter coefficients; finite, not all zero, matching length."""

    operator: Literal["<=", ">="]
    """The inequality direction."""

    rhs: Finite
    """The right-hand side of ``Σ coefficients[i] * value[i] (operator) rhs``."""

    @model_validator(mode="after")
    def _validate_structure(self) -> "LinearInequalityConstraintSpec":
        """Reject length mismatch, duplicate ids, and all-zero coefficients."""
        if len(self.coefficients) != len(self.parameter_ids):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients length "
                f"{len(self.coefficients)} must equal parameterIds length "
                f"{len(self.parameter_ids)}."
            )
        _validate_parameter_ids_unique(self)
        if all(c == 0 for c in self.coefficients):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients must not be all zero."
            )
        return self


class CardinalityConstraintSpec(_ConstraintBase):
    """A bound on how many parameters may take a non-zero value."""

    kind: Literal["Cardinality"] = "Cardinality"
    """The discriminator tag."""

    parameter_ids: Annotated[list[Ident], AfterValidator(_freeze_list), Field(min_length=2)]
    """The referenced parameter ids (at least two, unique)."""

    min_cardinality: int = Field(ge=0)
    """The minimum number of active parameters."""

    max_cardinality: int
    """The maximum number of active parameters (``min <= max <= len``)."""

    @model_validator(mode="after")
    def _validate_cardinality(self) -> "CardinalityConstraintSpec":
        """Reject bad bounds and duplicate parameter ids."""
        _validate_parameter_ids_unique(self)
        n = len(self.parameter_ids)
        if not (self.min_cardinality <= self.max_cardinality <= n):
            raise ValueError(
                f"Constraint {self.id!r}: requires min <= max <= "
                f"len(parameterIds), got min={self.min_cardinality}, "
                f"max={self.max_cardinality}, len={n}."
            )
        return self


ConstraintSpec = Annotated[
    Union[
        LinearEqualityConstraintSpec,
        LinearInequalityConstraintSpec,
        CardinalityConstraintSpec,
    ],
    Field(discriminator="kind"),
]
"""Discriminated union of constraint specifications, keyed on ``kind``."""


# OptimizationPolicy (§2.9) -------------------------------------------------


class SeedPolicy(StrEnum):
    """Whether the seed is user-fixed or auto-generated."""

    FIXED = "Fixed"
    AUTO_GENERATED = "AutoGenerated"


class TwoPhaseMetaConfig(_FrozenBase):
    """A cold-start recommender that switches to Bayesian optimization."""

    kind: Literal["TwoPhaseMeta"] = "TwoPhaseMeta"
    """The discriminator tag."""

    initial_recommender: Literal["RandomRecommender", "FPSRecommender"]
    """The cold-start recommender used before switching."""

    switch_after: int = Field(ge=1)
    """The cumulative observation count that triggers the switch."""

    remain_switched: bool
    """Whether the switch to the Bayesian phase is permanent."""

    acquisition_function: Literal["qLogEI", "qLogNEHVI", "qLogNParEGO"]
    """The acquisition function used in the Bayesian phase."""


class BotorchConfig(_FrozenBase):
    """A direct BoTorch recommender."""

    kind: Literal["Botorch"] = "Botorch"
    """The discriminator tag."""

    acquisition_function: Literal["qLogEI", "qLogNEHVI", "qLogNParEGO"]
    """The acquisition function."""


StrategyConfig = Annotated[
    Union[TwoPhaseMetaConfig, BotorchConfig],
    Field(discriminator="kind"),
]
"""Discriminated union of strategy configurations, keyed on ``kind``."""


class OptimizationPolicy(_FrozenBase):
    """The execution-side strategy carried by a :class:`CampaignRun`."""

    id: Ident
    """The stable identifier."""

    backend_name: str = "baybe"
    """The optimizer backend name (fixed to ``baybe`` in the MVP)."""

    batch_size: int = Field(ge=1)
    """The default number of candidates requested per round."""

    seed_policy: SeedPolicy
    """Whether the seed is fixed or auto-generated."""

    seed_value: int | None = None
    """The seed; required when ``seedPolicy='Fixed'``."""

    strategy_config: StrategyConfig
    """The typed strategy configuration."""

    @model_validator(mode="after")
    def _validate_seed(self) -> "OptimizationPolicy":
        """Require an explicit seed when the seed policy is ``Fixed``."""
        if self.seed_policy is SeedPolicy.FIXED and self.seed_value is None:
            raise ValueError("seedValue is required when seedPolicy is 'Fixed'.")
        return self


# CampaignDefinition (§2.1) & CampaignDefinitionRevision (§2.2) --------------


class CampaignDefinition(_Base):
    """The stateless container pointing at its head revision."""

    id: Ident
    """The stable identifier."""

    name: Ident
    """The platform-unique campaign name."""

    goal: str | None = None
    """The optional free-text goal statement."""

    head_revision_id: Ident
    """The id of the current head :class:`CampaignDefinitionRevision`."""

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""

    created_by: Ident
    """The creator identity."""

    updated_at: AwareDatetime
    """The timezone-aware timestamp of the last container update."""


class CampaignDefinitionRevision(_FrozenBase):
    """An immutable snapshot of a campaign's problem definition."""

    id: Ident
    """The stable identifier."""

    campaign_definition_id: Ident
    """The owning :class:`CampaignDefinition` id."""

    revision_number: int = Field(ge=1)
    """The monotonic per-container revision number (first is 1)."""

    parent_revision_id: Ident | None = None
    """The predecessor revision id; ``None`` iff ``revisionNumber == 1``."""

    parameters: Annotated[list[ParameterSpec], AfterValidator(_freeze_list), Field(min_length=1)]
    """The parameter specifications (at least one)."""

    outputs: Annotated[list[OutputSpec], AfterValidator(_freeze_list), Field(min_length=1)]
    """The output specifications (at least one)."""

    targets: Annotated[list[TargetSpec], AfterValidator(_freeze_list), Field(min_length=1)]
    """The target specifications (at least one)."""

    objective_policy: ObjectivePolicy
    """The single campaign-level objective policy."""

    constraints: Annotated[list[ConstraintSpec], AfterValidator(_freeze_list)] = Field(
        default_factory=_FrozenList
    )
    """The constraint specifications (possibly empty)."""

    constraints_confirmed: bool = False
    """Whether the user has confirmed the constraint set."""

    constraints_confirmed_at: AwareDatetime | None = None
    """The confirmation timestamp; required when ``constraintsConfirmed``."""

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""

    created_by: Ident
    """The creator identity."""

    @model_validator(mode="after")
    def _validate_structure(self) -> "CampaignDefinitionRevision":
        """Enforce the revision/parent invariant, confirmation, and unique ids."""
        if self.revision_number == 1 and self.parent_revision_id is not None:
            raise ValueError("The first revision must have parentRevisionId=None.")
        if self.revision_number > 1 and self.parent_revision_id is None:
            raise ValueError(
                "Revisions after the first must reference a parentRevisionId."
            )
        if self.constraints_confirmed and self.constraints_confirmed_at is None:
            raise ValueError(
                "constraintsConfirmedAt is required when constraintsConfirmed is True."
            )
        for label, items in (
            ("parameters", self.parameters),
            ("outputs", self.outputs),
            ("targets", self.targets),
            ("constraints", self.constraints),
        ):
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate id found in {label}.")
        return self


# CampaignRun (§2.3) --------------------------------------------------------


class RunStatus(StrEnum):
    """The lifecycle state of a :class:`CampaignRun` (§3.1)."""

    DRAFT = "Draft"
    DESIGN_SPACE_VALIDATED = "DesignSpaceValidated"
    RECOMMENDATIONS_PENDING = "RecommendationsPending"
    AWAITING_MEASUREMENTS = "AwaitingMeasurements"
    ROUND_CLOSED = "RoundClosed"
    COMPLETED = "Completed"
    ARCHIVED = "Archived"


class CampaignRun(_Base):
    """A single execution of a definition revision under one policy."""

    id: Ident
    """The stable identifier."""

    campaign_definition_id: Ident
    """The owning :class:`CampaignDefinition` id."""

    definition_revision_id: Ident
    """The pinned revision id (frozen after the first batch, §3.6)."""

    status: RunStatus
    """The current lifecycle state."""

    optimization_policy: OptimizationPolicy
    """The execution policy (frozen after the first batch, §3.6)."""

    round: int = Field(ge=0)
    """The number of closed rounds so far."""

    budget_total: int = Field(ge=1)
    """The total experiment budget."""

    budget_used: int = Field(ge=0)
    """The count of terminal experiment runs consumed."""

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""

    updated_at: AwareDatetime
    """The timezone-aware timestamp of the last update."""

    created_by: Ident
    """The creator identity."""

    @model_validator(mode="after")
    def _validate_budget(self) -> "CampaignRun":
        """Reject a consumed budget that exceeds the total."""
        if self.budget_used > self.budget_total:
            raise ValueError(
                f"budgetUsed ({self.budget_used}) must not exceed budgetTotal "
                f"({self.budget_total})."
            )
        return self


# ExperimentRound (§2.10) ---------------------------------------------------


class RoundStatus(StrEnum):
    """Whether an experiment round is open or closed."""

    OPEN = "Open"
    CLOSED = "Closed"


class ExperimentRound(_Base):
    """One round of experiments tied to a recommendation batch."""

    id: Ident
    """The stable identifier."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    round_number: int = Field(ge=1)
    """The round number, matching the batch's ``roundNumber``."""

    recommendation_batch_id: Ident
    """The originating :class:`RecommendationBatch` id."""

    opened_at: AwareDatetime
    """The timezone-aware timestamp the round was opened."""

    closed_at: AwareDatetime | None = None
    """The timezone-aware timestamp the round was closed, if closed."""

    status: RoundStatus
    """The open/closed status."""


# ExperimentRun (§2.11) -----------------------------------------------------


class ExperimentRunStatus(StrEnum):
    """The physical execution status of an experiment."""

    PENDING = "Pending"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


class ExperimentRun(_Base):
    """A single physical experiment execution."""

    id: Ident
    """The stable identifier."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    experiment_round_id: Ident
    """The owning :class:`ExperimentRound` id."""

    recommendation_candidate_id: str | None = None
    """The originating candidate id; ``None`` for manual experiments."""

    parameter_values: dict[str, str | Finite]
    """The value assigned to each configured parameter id."""

    status: ExperimentRunStatus
    """The physical execution status (not measurement readiness)."""

    executed_at: AwareDatetime | None = None
    """The execution timestamp; required when status is Completed/Failed."""

    executed_by: Ident | None = None
    """The executor identity; required when status is Completed/Failed."""

    notes: str | None = None
    """The optional free-text notes."""

    @model_validator(mode="after")
    def _validate_execution(self) -> "ExperimentRun":
        """Require execution metadata once the run has physically finished."""
        terminal = {ExperimentRunStatus.COMPLETED, ExperimentRunStatus.FAILED}
        if self.status in terminal and (
            self.executed_at is None or self.executed_by is None
        ):
            raise ValueError(
                f"executedAt and executedBy are required when status is "
                f"{self.status.value}."
            )
        return self


# Measurement (§2.12) -------------------------------------------------------


class MeasurementStatus(StrEnum):
    """Whether a measurement is a valid reading or an archived invalid one."""

    VALID = "Valid"
    INVALID = "Invalid"


class Measurement(_FrozenBase):
    """An immutable reading of one output for one experiment run."""

    id: Ident
    """The stable identifier."""

    experiment_run_id: Ident
    """The owning :class:`ExperimentRun` id."""

    output_id: Ident
    """The measured :class:`OutputSpec` id."""

    value: Finite
    """The measured value."""

    status: MeasurementStatus
    """Valid readings participate in fitting; invalid ones are archived only."""

    revision: int = Field(ge=1)
    """The version within ``(experimentRunId, outputId)`` (first is 1)."""

    supersedes_measurement_id: Ident | None = None
    """The superseded reading's id; ``None`` iff ``revision == 1``."""

    recorded_at: AwareDatetime
    """The timezone-aware recording timestamp."""

    recorded_by: Ident
    """The recorder identity."""

    notes: str | None = None
    """The optional free-text notes."""

    @model_validator(mode="after")
    def _validate_revision(self) -> "Measurement":
        """Tie the supersede pointer to the revision number."""
        if self.revision == 1 and self.supersedes_measurement_id is not None:
            raise ValueError(
                "The first measurement revision must have "
                "supersedesMeasurementId=None."
            )
        if self.revision > 1 and self.supersedes_measurement_id is None:
            raise ValueError(
                "Measurement revisions after the first must reference the "
                "superseded measurement."
            )
        return self


# RecommendationBatch (§2.13) -----------------------------------------------


class Environment(_FrozenBase):
    """The reproducibility environment captured with a batch."""

    python_version: Ident
    """The Python version string."""

    torch_version: Ident
    """The torch version string."""

    botorch_version: Ident
    """The BoTorch version string."""

    dependency_lock_hash: Ident
    """The lock-file content hash (e.g. ``sha256:...``)."""


class AlgorithmConfig(_FrozenBase):
    """The resolved algorithm configuration recorded with a batch."""

    backend_name: Ident
    """The backend name."""

    backend_version: Ident
    """The backend version."""

    backend_commit: Ident
    """The backend commit hash."""

    strategy_kind: Literal["TwoPhaseMeta", "Botorch"]
    """The strategy kind, aligned with ``strategyConfig.kind``."""

    hyperparameters: Annotated[dict[str, Any], AfterValidator(_freeze_dict)] = Field(
        default_factory=_FrozenDict
    )
    """The concrete hyperparameter values expanded from the strategy config."""

    acquisition_function: Ident
    """The acquisition function used."""

    seed: int
    """The concrete seed; auto-generated seeds are written back here."""

    environment: Environment
    """The reproducibility environment."""


class RecommendationCandidate(_FrozenBase):
    """A single proposed parameter configuration."""

    id: Ident
    """The stable identifier."""

    parameter_values: Annotated[dict[str, str | Finite], AfterValidator(_freeze_dict)]
    """The proposed value for each parameter id."""

    predicted_mean: Annotated[dict[str, Finite] | None, AfterValidator(_freeze_dict)] = None
    """The per-output predicted mean; ``None`` for model-free initial design."""

    predicted_sd: Annotated[dict[str, Finite] | None, AfterValidator(_freeze_dict)] = None
    """The per-output predicted standard deviation, when available."""

    desirability: Finite | None = None
    """The scalar desirability, when a Desirability objective applies."""


class BatchStatus(StrEnum):
    """The execution status of a recommendation batch."""

    PROPOSED = "Proposed"
    PARTIALLY_EXECUTED = "PartiallyExecuted"
    FULLY_EXECUTED = "FullyExecuted"
    SUPERSEDED = "Superseded"


class RecommendationBatch(_Base):
    """A persisted batch of candidates assembled by the application service."""

    id: Ident
    """The stable identifier."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    round_number: int = Field(ge=1)
    """The round number (1 for initial design, ``run.round + 1`` after)."""

    generated_at: AwareDatetime
    """The timezone-aware generation timestamp."""

    input_snapshot: dict[str, Any]
    """The deep-copied, self-contained inputs used to generate the batch."""

    algorithm_config: AlgorithmConfig
    """The resolved algorithm configuration."""

    candidates: Annotated[list[RecommendationCandidate], AfterValidator(_freeze_list), Field(min_length=1)]
    """The candidates; length equals the effective batch size."""

    status: BatchStatus
    """The batch execution status."""


# DecisionLog (§2.14) -------------------------------------------------------


class DecisionAction(StrEnum):
    """The append-only decision-log action vocabulary (§2.14)."""

    CAMPAIGN_CREATED = "CampaignCreated"
    DEFINITION_REVISION_CREATED = "DefinitionRevisionCreated"
    CONSTRAINTS_CONFIRMED = "ConstraintsConfirmed"
    DESIGN_SPACE_VALIDATED = "DesignSpaceValidated"
    DESIGN_SPACE_VALIDATION_FAILED = "DesignSpaceValidationFailed"
    REVISION_REPINNED = "RevisionRepinned"
    INITIAL_DESIGN_GENERATED = "InitialDesignGenerated"
    RECOMMENDATION_REQUESTED = "RecommendationRequested"
    EXPERIMENT_RUN_EXECUTED = "ExperimentRunExecuted"
    MEASUREMENT_RECORDED = "MeasurementRecorded"
    MEASUREMENT_SUPERSEDED = "MeasurementSuperseded"
    ROUND_CLOSED = "RoundClosed"
    ROUND_ABORTED = "RoundAborted"
    RUN_COMPLETED = "RunCompleted"
    RUN_ARCHIVED = "RunArchived"
    RUN_REOPENED = "RunReopened"


class DecisionLog(_FrozenBase):
    """An append-only audit record of a decision on a campaign run."""

    id: Ident
    """The stable identifier."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    timestamp: AwareDatetime
    """The timezone-aware timestamp of the action."""

    actor: Ident
    """The actor: a user id, ``agent:<name>``, or ``system``."""

    action: DecisionAction
    """The recorded action."""

    definition_revision_id: Ident
    """The revision the action pertains to."""

    payload: Annotated[dict[str, Any] | None, AfterValidator(_freeze_dict)] = None
    """The optional structured payload."""

    related_entity_id: str | None = None
    """The optional id of a related entity."""


# Agent v0 (conversational design-space editing) ----------------------------


class AgentThread(_Base):
    """A conversation thread scoped to one campaign run (one per run, MVP)."""

    id: Ident
    """The stable identifier."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""


class AgentMessageRole(StrEnum):
    """The author of an agent message."""

    USER = "user"
    ASSISTANT = "assistant"


class AgentMessage(_Base):
    """One message in an :class:`AgentThread`, from the user or the assistant."""

    id: Ident
    """The stable identifier."""

    thread_id: Ident
    """The owning :class:`AgentThread` id."""

    role: AgentMessageRole
    """Who authored the message."""

    content: str
    """The natural-language message text."""

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""


class AgentProposalStatus(StrEnum):
    """The lifecycle of a proposed agent action."""

    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    FAILED = "Failed"


class AgentProposal(_Base):
    """A structured action the agent proposed, awaiting user approval.

    ``payload`` is the serialized :class:`~backend.agent.contract.AgentAction`
    (the ``kind`` mirrored into the ``kind`` column). ``base_revision_id`` pins
    the run's ``definitionRevisionId`` at proposal time so a stale approval — one
    made after the campaign changed underneath it — can be rejected rather than
    silently overwriting newer work.
    """

    id: Ident
    """The stable identifier."""

    thread_id: Ident
    """The owning :class:`AgentThread` id."""

    campaign_run_id: Ident
    """The owning :class:`CampaignRun` id."""

    kind: str
    """The proposed action kind (mirrors ``payload['kind']``)."""

    payload: Annotated[dict[str, Any], AfterValidator(_freeze_dict)]
    """The serialized proposed action."""

    status: AgentProposalStatus
    """The proposal's lifecycle state."""

    base_revision_id: Ident
    """The run's ``definitionRevisionId`` when the proposal was created."""

    base_run_updated_at: AwareDatetime
    """The run's ``updatedAt`` when the proposal was created (concurrency token).

    Every run mutation (status, policy, or revision change) bumps ``updatedAt``,
    so this pins the run's full version at proposal time. Together with
    ``base_revision_id`` it lets an approval detect that the run moved — a status
    transition, a policy swap, or a re-pin — even when the revision id alone did
    not change, and reject the stale proposal instead of dispatching it.
    """

    created_at: AwareDatetime
    """The timezone-aware creation timestamp."""

    resolved_at: AwareDatetime | None = None
    """The timezone-aware approve/reject/fail timestamp, if resolved."""

    error: str | None = None
    """A human-readable failure message when ``status`` is ``Failed``."""


__all__ = [
    "AgentMessage",
    "AgentMessageRole",
    "AgentProposal",
    "AgentProposalStatus",
    "AgentThread",
    "AlgorithmConfig",
    "BatchStatus",
    "Bounds",
    "BotorchConfig",
    "CampaignDefinition",
    "CampaignDefinitionRevision",
    "CampaignRun",
    "CardinalityConstraintSpec",
    "CategoricalParameterSpec",
    "ConstraintSpec",
    "ContinuousParameterSpec",
    "Cutoffs",
    "DecisionAction",
    "DecisionLog",
    "DesirabilityEntry",
    "DesirabilityObjectivePolicy",
    "Direction",
    "DiscreteParameterSpec",
    "Environment",
    "ExperimentRound",
    "ExperimentRun",
    "ExperimentRunStatus",
    "Finite",
    "Ident",
    "LinearEqualityConstraintSpec",
    "LinearInequalityConstraintSpec",
    "Measurement",
    "MeasurementStatus",
    "ObjectivePolicy",
    "OptimizationPolicy",
    "OutputSpec",
    "ParameterSpec",
    "ParetoObjectivePolicy",
    "PositiveFinite",
    "RecommendationBatch",
    "RecommendationCandidate",
    "RoundStatus",
    "RunStatus",
    "Scalarizer",
    "SeedPolicy",
    "SingleObjectivePolicy",
    "StrategyConfig",
    "TargetSpec",
    "TwoPhaseMetaConfig",
    "WeightingMode",
]
