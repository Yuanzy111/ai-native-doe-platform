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
"""

from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class _Base(BaseModel):
    """Base for mutable domain models: camelCase aliases, no extra fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class _FrozenBase(BaseModel):
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

    lower: float
    """The inclusive lower bound."""

    upper: float
    """The inclusive upper bound."""

    stepsize: float | None = None
    """Optional discretization step; must be positive when given."""


class _ParameterBase(_FrozenBase):
    """Shared fields of every parameter specification."""

    id: str = Field(min_length=1)
    """The stable identifier used by constraints and value maps."""

    name: str = Field(min_length=1)
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
        """Reject empty or inverted intervals and non-positive step sizes."""
        if self.bounds.lower >= self.bounds.upper:
            raise ValueError(
                f"Continuous parameter {self.name!r} requires lower < upper, "
                f"got lower={self.bounds.lower}, upper={self.bounds.upper}."
            )
        if self.bounds.stepsize is not None and self.bounds.stepsize <= 0:
            raise ValueError(
                f"Continuous parameter {self.name!r} stepsize must be positive, "
                f"got {self.bounds.stepsize}."
            )
        return self


class DiscreteParameterSpec(_ParameterBase):
    """A parameter drawn from a finite set of numeric levels."""

    type: Literal["Discrete"] = "Discrete"
    """The discriminator tag."""

    values: list[float] = Field(min_length=1)
    """The allowed numeric levels, deduplicated and sorted ascending."""

    @field_validator("values")
    @classmethod
    def _dedup_sort(cls, value: list[float]) -> list[float]:
        """Deduplicate and sort the numeric levels (documented normalization)."""
        return sorted(set(value))


class CategoricalParameterSpec(_ParameterBase):
    """A parameter drawn from a finite set of labels."""

    type: Literal["Categorical"] = "Categorical"
    """The discriminator tag."""

    values: list[str] = Field(min_length=1)
    """The allowed labels: non-blank, deduplicated, case-sensitive, ordered."""

    @field_validator("values")
    @classmethod
    def _dedup_preserve_order(cls, value: list[str]) -> list[str]:
        """Reject blank labels and drop case-sensitive duplicates in order."""
        seen: list[str] = []
        for label in value:
            if not label.strip():
                raise ValueError("Categorical values must be non-blank.")
            if label not in seen:
                seen.append(label)
        return seen


ParameterSpec = Annotated[
    Union[ContinuousParameterSpec, DiscreteParameterSpec, CategoricalParameterSpec],
    Field(discriminator="type"),
]
"""Discriminated union of parameter specifications, keyed on ``type``."""


# OutputSpec (§2.5) ---------------------------------------------------------


class OutputSpec(_FrozenBase):
    """A measurable quantity, carrying no optimization direction."""

    id: str = Field(min_length=1)
    """The stable identifier referenced by targets and measurements."""

    name: str = Field(min_length=1)
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

    id: str = Field(min_length=1)
    """The stable identifier referenced by the objective policy."""

    output_id: str = Field(min_length=1)
    """The referenced :class:`OutputSpec` id."""

    direction: Direction
    """The optimization direction (``Target``/``CloseToTarget`` are v1+)."""

    target_value: float | None = None
    """Reserved for ``direction='Target'`` in v1+; unused in the MVP."""


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

    lower: float
    """The value mapped to one end of the ramp."""

    upper: float
    """The value mapped to the other end of the ramp."""


class DesirabilityEntry(_FrozenBase):
    """One target's contribution to a Desirability objective."""

    target_id: str = Field(min_length=1)
    """The referenced :class:`TargetSpec` id."""

    transformation: Literal["NormalizedRamp"] = "NormalizedRamp"
    """The MVP's only transformation (Target/Triangular/Bell are v1)."""

    cutoffs: Cutoffs
    """The explicit scaling cutoffs; no runtime guessing is permitted."""

    weight: float
    """The relative weight; forced equal when ``weightingMode='equal'``."""


class SingleObjectivePolicy(_FrozenBase):
    """A single-target objective."""

    kind: Literal["Single"] = "Single"
    """The discriminator tag."""

    target_id: str = Field(min_length=1)
    """The single referenced target; requires ``len(targets) == 1``."""


class DesirabilityObjectivePolicy(_FrozenBase):
    """A scalarized multi-target Desirability objective."""

    kind: Literal["Desirability"] = "Desirability"
    """The discriminator tag."""

    entries: list[DesirabilityEntry] = Field(min_length=1)
    """One entry per target; must cover every target one-to-one."""

    weighting_mode: WeightingMode
    """Whether weights are explicit or forced equal by the validator."""

    scalarizer: Scalarizer = Scalarizer.GEOM_MEAN
    """The scalarization operator (defaults to ``GEOM_MEAN`` like BayBE)."""


class ParetoObjectivePolicy(_FrozenBase):
    """A multi-target Pareto objective (no scalarization)."""

    kind: Literal["Pareto"] = "Pareto"
    """The discriminator tag."""

    target_ids: list[str] = Field(min_length=2)
    """At least two referenced targets; produces a Pareto frontier."""


ObjectivePolicy = Annotated[
    Union[SingleObjectivePolicy, DesirabilityObjectivePolicy, ParetoObjectivePolicy],
    Field(discriminator="kind"),
]
"""Discriminated union of objective policies, keyed on ``kind``."""


# ConstraintSpec (§2.8) -----------------------------------------------------


class _ConstraintBase(_FrozenBase):
    """Shared fields of every constraint specification."""

    id: str = Field(min_length=1)
    """The stable identifier."""

    resolved_at: str | None = None
    """The optional ISO-8601 timestamp at which the constraint was resolved."""


class LinearEqualityConstraintSpec(_ConstraintBase):
    """A linear equality over parameter values."""

    kind: Literal["LinearEquality"] = "LinearEquality"
    """The discriminator tag."""

    parameter_ids: list[str] = Field(min_length=2)
    """The referenced parameter ids (at least two)."""

    coefficients: list[float]
    """The per-parameter coefficients; must match ``parameterIds`` length."""

    rhs: float
    """The right-hand side of ``Σ coefficients[i] * value[i] = rhs``."""

    @model_validator(mode="after")
    def _validate_length(self) -> "LinearEqualityConstraintSpec":
        """Reject coefficient/parameter length mismatches."""
        if len(self.coefficients) != len(self.parameter_ids):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients length "
                f"{len(self.coefficients)} must equal parameterIds length "
                f"{len(self.parameter_ids)}."
            )
        return self


class LinearInequalityConstraintSpec(_ConstraintBase):
    """A linear inequality over parameter values."""

    kind: Literal["LinearInequality"] = "LinearInequality"
    """The discriminator tag."""

    parameter_ids: list[str] = Field(min_length=2)
    """The referenced parameter ids (at least two)."""

    coefficients: list[float]
    """The per-parameter coefficients; must match ``parameterIds`` length."""

    operator: Literal["<=", ">="]
    """The inequality direction."""

    rhs: float
    """The right-hand side of ``Σ coefficients[i] * value[i] (operator) rhs``."""

    @model_validator(mode="after")
    def _validate_length(self) -> "LinearInequalityConstraintSpec":
        """Reject coefficient/parameter length mismatches."""
        if len(self.coefficients) != len(self.parameter_ids):
            raise ValueError(
                f"Constraint {self.id!r}: coefficients length "
                f"{len(self.coefficients)} must equal parameterIds length "
                f"{len(self.parameter_ids)}."
            )
        return self


class CardinalityConstraintSpec(_ConstraintBase):
    """A bound on how many parameters may take a non-zero value."""

    kind: Literal["Cardinality"] = "Cardinality"
    """The discriminator tag."""

    parameter_ids: list[str] = Field(min_length=2)
    """The referenced parameter ids (at least two)."""

    min_cardinality: int = Field(ge=0)
    """The minimum number of active parameters."""

    max_cardinality: int
    """The maximum number of active parameters (``min <= max <= len``)."""

    @model_validator(mode="after")
    def _validate_cardinality(self) -> "CardinalityConstraintSpec":
        """Reject cardinality bounds outside ``0 <= min <= max <= len``."""
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

    id: str = Field(min_length=1)
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

    id: str = Field(min_length=1)
    """The stable identifier."""

    name: str = Field(min_length=1)
    """The platform-unique campaign name."""

    goal: str | None = None
    """The optional free-text goal statement."""

    head_revision_id: str = Field(min_length=1)
    """The id of the current head :class:`CampaignDefinitionRevision`."""

    created_at: str
    """The ISO-8601 creation timestamp."""

    created_by: str
    """The creator identity."""

    updated_at: str
    """The ISO-8601 timestamp of the last container update."""


class CampaignDefinitionRevision(_FrozenBase):
    """An immutable snapshot of a campaign's problem definition."""

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_definition_id: str = Field(min_length=1)
    """The owning :class:`CampaignDefinition` id."""

    revision_number: int = Field(ge=1)
    """The monotonic per-container revision number (first is 1)."""

    parent_revision_id: str | None = None
    """The predecessor revision id; ``None`` iff ``revisionNumber == 1``."""

    parameters: list[ParameterSpec] = Field(min_length=1)
    """The parameter specifications (at least one)."""

    outputs: list[OutputSpec] = Field(min_length=1)
    """The output specifications (at least one)."""

    targets: list[TargetSpec] = Field(min_length=1)
    """The target specifications (at least one)."""

    objective_policy: ObjectivePolicy
    """The single campaign-level objective policy."""

    constraints: list[ConstraintSpec] = Field(default_factory=list)
    """The constraint specifications (possibly empty)."""

    constraints_confirmed: bool = False
    """Whether the user has confirmed the constraint set."""

    constraints_confirmed_at: str | None = None
    """The confirmation timestamp; required when ``constraintsConfirmed``."""

    created_at: str
    """The ISO-8601 creation timestamp."""

    created_by: str
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

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_definition_id: str = Field(min_length=1)
    """The owning :class:`CampaignDefinition` id."""

    definition_revision_id: str = Field(min_length=1)
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

    created_at: str
    """The ISO-8601 creation timestamp."""

    updated_at: str
    """The ISO-8601 timestamp of the last update."""

    created_by: str
    """The creator identity."""


# ExperimentRound (§2.10) ---------------------------------------------------


class RoundStatus(StrEnum):
    """Whether an experiment round is open or closed."""

    OPEN = "Open"
    CLOSED = "Closed"


class ExperimentRound(_Base):
    """One round of experiments tied to a recommendation batch."""

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_run_id: str = Field(min_length=1)
    """The owning :class:`CampaignRun` id."""

    round_number: int = Field(ge=1)
    """The round number, matching the batch's ``roundNumber``."""

    recommendation_batch_id: str = Field(min_length=1)
    """The originating :class:`RecommendationBatch` id."""

    experiment_run_ids: list[str] = Field(default_factory=list)
    """The experiment run ids, growing as executions are recorded."""

    opened_at: str
    """The ISO-8601 timestamp the round was opened."""

    closed_at: str | None = None
    """The ISO-8601 timestamp the round was closed, if closed."""

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

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_run_id: str = Field(min_length=1)
    """The owning :class:`CampaignRun` id."""

    experiment_round_id: str = Field(min_length=1)
    """The owning :class:`ExperimentRound` id."""

    recommendation_candidate_id: str | None = None
    """The originating candidate id; ``None`` for manual experiments."""

    parameter_values: dict[str, str | float]
    """The value assigned to each configured parameter id."""

    status: ExperimentRunStatus
    """The physical execution status (not measurement readiness)."""

    executed_at: str | None = None
    """The execution timestamp; required when status is Completed/Failed."""

    executed_by: str | None = None
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

    id: str = Field(min_length=1)
    """The stable identifier."""

    experiment_run_id: str = Field(min_length=1)
    """The owning :class:`ExperimentRun` id."""

    output_id: str = Field(min_length=1)
    """The measured :class:`OutputSpec` id."""

    value: float
    """The measured value."""

    status: MeasurementStatus
    """Valid readings participate in fitting; invalid ones are archived only."""

    revision: int = Field(ge=1)
    """The version within ``(experimentRunId, outputId)`` (first is 1)."""

    supersedes_measurement_id: str | None = None
    """The superseded reading's id; ``None`` iff ``revision == 1``."""

    recorded_at: str
    """The ISO-8601 recording timestamp."""

    recorded_by: str
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

    python_version: str = Field(min_length=1)
    """The Python version string."""

    torch_version: str = Field(min_length=1)
    """The torch version string."""

    botorch_version: str = Field(min_length=1)
    """The BoTorch version string."""

    dependency_lock_hash: str = Field(min_length=1)
    """The lock-file content hash (e.g. ``sha256:...``)."""


class AlgorithmConfig(_FrozenBase):
    """The resolved algorithm configuration recorded with a batch."""

    backend_name: str = Field(min_length=1)
    """The backend name."""

    backend_version: str = Field(min_length=1)
    """The backend version."""

    backend_commit: str = Field(min_length=1)
    """The backend commit hash."""

    strategy_kind: Literal["TwoPhaseMeta", "Botorch"]
    """The strategy kind, aligned with ``strategyConfig.kind``."""

    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    """The concrete hyperparameter values expanded from the strategy config."""

    acquisition_function: str = Field(min_length=1)
    """The acquisition function used."""

    seed: int
    """The concrete seed; auto-generated seeds are written back here."""

    environment: Environment
    """The reproducibility environment."""


class RecommendationCandidate(_FrozenBase):
    """A single proposed parameter configuration."""

    id: str = Field(min_length=1)
    """The stable identifier."""

    parameter_values: dict[str, str | float]
    """The proposed value for each parameter id."""

    predicted_mean: dict[str, float] | None = None
    """The per-output predicted mean; ``None`` for model-free initial design."""

    predicted_sd: dict[str, float] | None = None
    """The per-output predicted standard deviation, when available."""

    desirability: float | None = None
    """The scalar desirability, when a Desirability objective applies."""


class BatchStatus(StrEnum):
    """The execution status of a recommendation batch."""

    PROPOSED = "Proposed"
    PARTIALLY_EXECUTED = "PartiallyExecuted"
    FULLY_EXECUTED = "FullyExecuted"
    SUPERSEDED = "Superseded"


class RecommendationBatch(_Base):
    """A persisted batch of candidates assembled by the application service."""

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_run_id: str = Field(min_length=1)
    """The owning :class:`CampaignRun` id."""

    round_number: int = Field(ge=1)
    """The round number (1 for initial design, ``run.round + 1`` after)."""

    generated_at: str
    """The ISO-8601 generation timestamp."""

    input_snapshot: dict[str, Any]
    """The deep-copied, self-contained inputs used to generate the batch."""

    algorithm_config: AlgorithmConfig
    """The resolved algorithm configuration."""

    candidates: list[RecommendationCandidate] = Field(min_length=1)
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

    id: str = Field(min_length=1)
    """The stable identifier."""

    campaign_run_id: str = Field(min_length=1)
    """The owning :class:`CampaignRun` id."""

    timestamp: str
    """The ISO-8601 timestamp of the action."""

    actor: str = Field(min_length=1)
    """The actor: a user id, ``agent:<name>``, or ``system``."""

    action: DecisionAction
    """The recorded action."""

    definition_revision_id: str = Field(min_length=1)
    """The revision the action pertains to."""

    payload: dict[str, Any] | None = None
    """The optional structured payload."""

    related_entity_id: str | None = None
    """The optional id of a related entity."""


__all__ = [
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
    "LinearEqualityConstraintSpec",
    "LinearInequalityConstraintSpec",
    "Measurement",
    "MeasurementStatus",
    "ObjectivePolicy",
    "OptimizationPolicy",
    "OutputSpec",
    "ParameterSpec",
    "ParetoObjectivePolicy",
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
