"""Backend-agnostic deterministic validation (architecture v0.2, §3-§4).

Everything here is a pure function of its inputs: no I/O, no persistence, no
backend calls. Three concerns live together because they share the same
domain vocabulary:

* ``validate_definition`` — the design-space readiness gate (§4), reporting
  referential/semantic issues rather than raising, so a Draft revision can be
  inspected while still inconsistent.
* ``validate_candidates`` — the post-candidate result check (§4.1), reusing the
  same constraint evaluator as the definition's executability check.
* the :class:`CampaignRun` state machine (§3.2) and the measurement
  supersede-chain helpers (§2.12).

Backend-specific judgments (which parameter/constraint kinds a given optimizer
supports, minimum-observation thresholds) intentionally stay out of this
module; they belong to the adapter layer.
"""

from collections import Counter
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from backend.domain.models import (
    CampaignDefinitionRevision,
    CardinalityConstraintSpec,
    CategoricalParameterSpec,
    ContinuousParameterSpec,
    DesirabilityObjectivePolicy,
    DiscreteParameterSpec,
    LinearEqualityConstraintSpec,
    LinearInequalityConstraintSpec,
    Measurement,
    MeasurementStatus,
    ParetoObjectivePolicy,
    RecommendationCandidate,
    RunStatus,
    SingleObjectivePolicy,
    WeightingMode,
)

TOLERANCE = 1e-9
"""The absolute tolerance for numeric equality, bound alignment, and constraints."""


class Severity(StrEnum):
    """The severity of a validation issue."""

    BLOCKING = "blocking"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """A single validation finding (§4)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True
    )

    code: str
    """The stable machine-readable issue code."""

    message: str
    """The human-readable explanation."""

    severity: Severity
    """Whether the issue blocks progress or is merely advisory."""

    related_entity_id: str | None = None
    """The id of the entity the issue pertains to, when applicable."""


class ValidationResult(BaseModel):
    """The outcome of a validation pass (§4)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True
    )

    issues: tuple[ValidationIssue, ...] = ()
    """The findings; ``ok`` is derived from their severities."""

    @property
    def ok(self) -> bool:
        """Whether no blocking issue is present."""
        return not any(i.severity is Severity.BLOCKING for i in self.issues)

    @property
    def blocking_issues(self) -> tuple[ValidationIssue, ...]:
        """The subset of issues with blocking severity."""
        return tuple(i for i in self.issues if i.severity is Severity.BLOCKING)


class StateTransitionError(Exception):
    """Raised when a :class:`CampaignRun` state transition is not permitted."""


# validate_definition (§4) --------------------------------------------------


def validate_definition(revision: CampaignDefinitionRevision) -> ValidationResult:
    """Run the backend-agnostic design-space checks on a definition revision.

    Args:
        revision: The immutable definition revision to validate.

    Returns:
        A :class:`ValidationResult`; ``ok`` is false when any blocking issue is
        present (constraints unconfirmed, dangling references, inconsistent
        objective policy, illegal Desirability cutoffs, or non-executable
        constraints).
    """
    issues: list[ValidationIssue] = []

    if not revision.constraints_confirmed:
        issues.append(
            ValidationIssue(
                code="CONSTRAINTS_NOT_CONFIRMED",
                message="Constraints must be confirmed before validation.",
                severity=Severity.BLOCKING,
            )
        )

    issues.extend(_validate_names(revision))
    issues.extend(_validate_targets(revision))
    issues.extend(_validate_objective_policy(revision))
    issues.extend(_validate_constraint_executability(revision))

    return ValidationResult(issues=tuple(issues))


def _validate_names(
    revision: CampaignDefinitionRevision,
) -> list[ValidationIssue]:
    """Flag case-insensitive duplicate parameter or output names."""
    issues: list[ValidationIssue] = []
    for label, code, items in (
        ("parameter", "DUPLICATE_PARAMETER_NAME", revision.parameters),
        ("output", "DUPLICATE_OUTPUT_NAME", revision.outputs),
    ):
        counts = Counter(item.name.casefold() for item in items)
        for item in items:
            if counts[item.name.casefold()] > 1:
                issues.append(
                    ValidationIssue(
                        code=code,
                        message=f"Duplicate {label} name {item.name!r} "
                        "(names must be unique, case-insensitive).",
                        severity=Severity.BLOCKING,
                        related_entity_id=item.id,
                    )
                )
    return issues


def _validate_targets(
    revision: CampaignDefinitionRevision,
) -> list[ValidationIssue]:
    """Flag targets referencing unknown outputs or outputs not covered once."""
    issues: list[ValidationIssue] = []
    output_ids = {o.id for o in revision.outputs}

    for target in revision.targets:
        if target.output_id not in output_ids:
            issues.append(
                ValidationIssue(
                    code="TARGET_UNKNOWN_OUTPUT",
                    message=f"Target {target.id!r} references unknown output "
                    f"{target.output_id!r}.",
                    severity=Severity.BLOCKING,
                    related_entity_id=target.id,
                )
            )

    reference_counts = Counter(t.output_id for t in revision.targets)
    for output in revision.outputs:
        count = reference_counts.get(output.id, 0)
        if count != 1:
            issues.append(
                ValidationIssue(
                    code="OUTPUT_TARGET_CARDINALITY",
                    message=f"Output {output.id!r} must be referenced by exactly "
                    f"one target, found {count}.",
                    severity=Severity.BLOCKING,
                    related_entity_id=output.id,
                )
            )
    return issues


def _validate_objective_policy(
    revision: CampaignDefinitionRevision,
) -> list[ValidationIssue]:
    """Check the objective policy against the declared targets (§2.7)."""
    issues: list[ValidationIssue] = []
    policy = revision.objective_policy
    target_ids = {t.id for t in revision.targets}

    def _unknown(target_id: str) -> None:
        issues.append(
            ValidationIssue(
                code="OBJECTIVE_UNKNOWN_TARGET",
                message=f"Objective policy references unknown target "
                f"{target_id!r}.",
                severity=Severity.BLOCKING,
                related_entity_id=target_id,
            )
        )

    if isinstance(policy, SingleObjectivePolicy):
        if policy.target_id not in target_ids:
            _unknown(policy.target_id)
        if len(revision.targets) != 1:
            issues.append(
                ValidationIssue(
                    code="OBJECTIVE_TARGET_COUNT",
                    message="Single objective requires exactly one target, "
                    f"found {len(revision.targets)}.",
                    severity=Severity.BLOCKING,
                )
            )
    elif isinstance(policy, DesirabilityObjectivePolicy):
        entry_ids = [e.target_id for e in policy.entries]
        for target_id in entry_ids:
            if target_id not in target_ids:
                _unknown(target_id)
        if set(entry_ids) != target_ids or len(entry_ids) != len(revision.targets):
            issues.append(
                ValidationIssue(
                    code="DESIRABILITY_COVERAGE",
                    message="Desirability entries must cover every target "
                    "exactly once.",
                    severity=Severity.BLOCKING,
                )
            )
        for entry in policy.entries:
            if entry.cutoffs.lower >= entry.cutoffs.upper:
                issues.append(
                    ValidationIssue(
                        code="DESIRABILITY_CUTOFFS_INVALID",
                        message=f"Desirability cutoffs for target "
                        f"{entry.target_id!r} require lower < upper, got "
                        f"lower={entry.cutoffs.lower}, upper={entry.cutoffs.upper}.",
                        severity=Severity.BLOCKING,
                        related_entity_id=entry.target_id,
                    )
                )
        if policy.weighting_mode is WeightingMode.EQUAL and policy.entries:
            weights = [e.weight for e in policy.entries]
            if max(weights) - min(weights) > TOLERANCE:
                issues.append(
                    ValidationIssue(
                        code="DESIRABILITY_WEIGHTS_NOT_EQUAL",
                        message="weightingMode='equal' requires all entry "
                        "weights to be equal.",
                        severity=Severity.BLOCKING,
                    )
                )
    elif isinstance(policy, ParetoObjectivePolicy):
        for target_id in policy.target_ids:
            if target_id not in target_ids:
                _unknown(target_id)
        if set(policy.target_ids) != target_ids or len(policy.target_ids) != len(
            revision.targets
        ):
            issues.append(
                ValidationIssue(
                    code="PARETO_COVERAGE",
                    message="Pareto target ids must cover every target exactly "
                    "once.",
                    severity=Severity.BLOCKING,
                )
            )

    return issues


def _validate_constraint_executability(
    revision: CampaignDefinitionRevision,
) -> list[ValidationIssue]:
    """Derive whether each constraint is currently executable (§2.8)."""
    issues: list[ValidationIssue] = []
    parameters = {p.id: p for p in revision.parameters}

    for constraint in revision.constraints:
        missing = [pid for pid in constraint.parameter_ids if pid not in parameters]
        if missing:
            issues.append(
                ValidationIssue(
                    code="CONSTRAINT_UNKNOWN_PARAMETER",
                    message=f"Constraint {constraint.id!r} references unknown "
                    f"parameter(s) {missing}.",
                    severity=Severity.BLOCKING,
                    related_entity_id=constraint.id,
                )
            )
        non_numeric = [
            pid
            for pid in constraint.parameter_ids
            if isinstance(parameters.get(pid), CategoricalParameterSpec)
        ]
        if non_numeric:
            issues.append(
                ValidationIssue(
                    code="CONSTRAINT_NON_NUMERIC_PARAMETER",
                    message=f"Constraint {constraint.id!r} references non-numeric "
                    f"parameter(s) {non_numeric}; linear and cardinality "
                    "constraints require continuous or discrete parameters.",
                    severity=Severity.BLOCKING,
                    related_entity_id=constraint.id,
                )
            )
        if isinstance(constraint, CardinalityConstraintSpec):
            n = len(constraint.parameter_ids)
            if constraint.min_cardinality == 0 and constraint.max_cardinality == n:
                issues.append(
                    ValidationIssue(
                        code="CARDINALITY_EMPTY",
                        message=f"Cardinality constraint {constraint.id!r} with "
                        "min=0 and max=len is a no-op and is not allowed.",
                        severity=Severity.BLOCKING,
                        related_entity_id=constraint.id,
                    )
                )
    return issues


# validate_candidates (§4.1 post-candidate result check) --------------------


def validate_candidates(
    revision: CampaignDefinitionRevision,
    candidates: list[RecommendationCandidate],
) -> ValidationResult:
    """Check adapter-returned candidates before a batch is assembled (§4.1).

    Args:
        revision: The definition revision the candidates were generated for.
        candidates: The candidates returned by the adapter.

    Returns:
        A :class:`ValidationResult`; any type, bound, allowed-value, constraint,
        or in-batch duplicate violation is blocking, so the whole batch is
        rejected rather than partially persisted.
    """
    issues: list[ValidationIssue] = []
    parameters = {p.id: p for p in revision.parameters}

    for candidate in candidates:
        issues.extend(_validate_candidate_values(parameters, candidate))
        issues.extend(
            _validate_candidate_constraints(revision, candidate)
        )

    issues.extend(_validate_candidate_duplicates(revision, candidates))
    return ValidationResult(issues=tuple(issues))


def _validate_candidate_values(
    parameters: dict, candidate: RecommendationCandidate
) -> list[ValidationIssue]:
    """Check one candidate's key set, value types, bounds, and allowed values."""
    issues: list[ValidationIssue] = []
    values = candidate.parameter_values

    if set(values) != set(parameters):
        issues.append(
            ValidationIssue(
                code="CANDIDATE_KEY_MISMATCH",
                message="Candidate parameter keys must match the revision "
                f"parameters exactly; got {sorted(values)}, expected "
                f"{sorted(parameters)}.",
                severity=Severity.BLOCKING,
                related_entity_id=candidate.id,
            )
        )
        return issues

    for pid, value in values.items():
        spec = parameters[pid]
        if isinstance(spec, ContinuousParameterSpec):
            if not _is_number(value):
                issues.append(
                    _type_issue(candidate.id, pid, "a number")
                )
                continue
            if not (spec.bounds.lower - TOLERANCE <= value <= spec.bounds.upper + TOLERANCE):
                issues.append(
                    ValidationIssue(
                        code="CANDIDATE_OUT_OF_BOUNDS",
                        message=f"Candidate value {value} for {pid!r} is outside "
                        f"[{spec.bounds.lower}, {spec.bounds.upper}].",
                        severity=Severity.BLOCKING,
                        related_entity_id=candidate.id,
                    )
                )
            if spec.bounds.stepsize is not None:
                steps = (value - spec.bounds.lower) / spec.bounds.stepsize
                if abs(steps - round(steps)) > TOLERANCE:
                    issues.append(
                        ValidationIssue(
                            code="CANDIDATE_STEP_MISALIGNED",
                            message=f"Candidate value {value} for {pid!r} is not "
                            f"aligned to stepsize {spec.bounds.stepsize}.",
                            severity=Severity.BLOCKING,
                            related_entity_id=candidate.id,
                        )
                    )
        elif isinstance(spec, DiscreteParameterSpec):
            if not _is_number(value):
                issues.append(_type_issue(candidate.id, pid, "a number"))
                continue
            if not any(abs(value - allowed) <= TOLERANCE for allowed in spec.values):
                issues.append(
                    ValidationIssue(
                        code="CANDIDATE_NOT_ALLOWED",
                        message=f"Candidate value {value} for {pid!r} is not among "
                        f"the allowed discrete values {spec.values}.",
                        severity=Severity.BLOCKING,
                        related_entity_id=candidate.id,
                    )
                )
        elif isinstance(spec, CategoricalParameterSpec):
            if not isinstance(value, str):
                issues.append(_type_issue(candidate.id, pid, "a string"))
                continue
            if value not in spec.values:
                issues.append(
                    ValidationIssue(
                        code="CANDIDATE_NOT_ALLOWED",
                        message=f"Candidate value {value!r} for {pid!r} is not among "
                        f"the allowed categories {spec.values}.",
                        severity=Severity.BLOCKING,
                        related_entity_id=candidate.id,
                    )
                )
    return issues


def _validate_candidate_constraints(
    revision: CampaignDefinitionRevision, candidate: RecommendationCandidate
) -> list[ValidationIssue]:
    """Check that one candidate satisfies every constraint (§4.1)."""
    issues: list[ValidationIssue] = []
    for constraint in revision.constraints:
        satisfied = _constraint_satisfied(constraint, candidate.parameter_values)
        if satisfied is False:
            issues.append(
                ValidationIssue(
                    code="CANDIDATE_CONSTRAINT_VIOLATED",
                    message=f"Candidate {candidate.id!r} violates constraint "
                    f"{constraint.id!r}.",
                    severity=Severity.BLOCKING,
                    related_entity_id=candidate.id,
                )
            )
        elif satisfied is None:
            issues.append(
                ValidationIssue(
                    code="CANDIDATE_CONSTRAINT_UNDECIDABLE",
                    message=f"Constraint {constraint.id!r} cannot be evaluated for "
                    f"candidate {candidate.id!r} (missing or non-numeric value).",
                    severity=Severity.BLOCKING,
                    related_entity_id=candidate.id,
                )
            )
    return issues


def _validate_candidate_duplicates(
    revision: CampaignDefinitionRevision,
    candidates: list[RecommendationCandidate],
) -> list[ValidationIssue]:
    """Flag two in-batch candidates whose parameter vectors coincide (§4.1)."""
    issues: list[ValidationIssue] = []
    param_ids = [p.id for p in revision.parameters]
    keys: list[tuple] = []
    for candidate in candidates:
        keys.append(_candidate_key(param_ids, candidate.parameter_values))
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if _keys_equal(keys[i], keys[j]):
                issues.append(
                    ValidationIssue(
                        code="CANDIDATE_DUPLICATE",
                        message=f"Candidates {candidates[i].id!r} and "
                        f"{candidates[j].id!r} have identical parameter vectors.",
                        severity=Severity.BLOCKING,
                        related_entity_id=candidates[j].id,
                    )
                )
    return issues


def _constraint_satisfied(constraint, values: dict) -> bool | None:
    """Return whether ``values`` satisfies ``constraint`` (``None`` if undecidable).

    Returns ``None`` when a referenced parameter is missing from ``values`` —
    executability is a separate concern handled by ``validate_definition``.
    """
    if isinstance(constraint, (LinearEqualityConstraintSpec, LinearInequalityConstraintSpec)):
        total = 0.0
        for coefficient, pid in zip(constraint.coefficients, constraint.parameter_ids):
            if pid not in values or not _is_number(values[pid]):
                return None
            total += coefficient * values[pid]
        if isinstance(constraint, LinearEqualityConstraintSpec):
            return abs(total - constraint.rhs) <= TOLERANCE
        if constraint.operator == "<=":
            return total <= constraint.rhs + TOLERANCE
        return total >= constraint.rhs - TOLERANCE
    if isinstance(constraint, CardinalityConstraintSpec):
        active = 0
        for pid in constraint.parameter_ids:
            if pid not in values or not _is_number(values[pid]):
                return None
            if abs(values[pid]) > TOLERANCE:
                active += 1
        return constraint.min_cardinality <= active <= constraint.max_cardinality
    return None


def _candidate_key(param_ids: list[str], values: dict) -> tuple:
    """Build a comparable key over parameters in a fixed order."""
    return tuple(values.get(pid) for pid in param_ids)


def _keys_equal(left: tuple, right: tuple) -> bool:
    """Compare two candidate keys with numeric tolerance."""
    for a, b in zip(left, right):
        if _is_number(a) and _is_number(b):
            if abs(a - b) > TOLERANCE:
                return False
        elif a != b:
            return False
    return True


def _is_number(value) -> bool:
    """Return whether ``value`` is a real number (excluding ``bool``)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_issue(candidate_id: str, pid: str, expected: str) -> ValidationIssue:
    """Build a candidate type-mismatch issue."""
    return ValidationIssue(
        code="CANDIDATE_TYPE_MISMATCH",
        message=f"Candidate value for {pid!r} must be {expected}.",
        severity=Severity.BLOCKING,
        related_entity_id=candidate_id,
    )


# CampaignRun state machine (§3.2) ------------------------------------------


class RunEvent(StrEnum):
    """The events that drive :class:`CampaignRun` state transitions (§3.2)."""

    VALIDATE_DEFINITION_PASS = "validate_definition_pass"
    VALIDATE_DEFINITION_FAIL = "validate_definition_fail"
    EDIT_DEFINITION = "edit_definition"
    GENERATE_INITIAL_DESIGN = "generate_initial_design"
    ALL_RUNS_TERMINAL = "all_runs_terminal"
    ABORT_ROUND = "abort_round"
    CLOSE_ROUND = "close_round"
    RECOMMEND = "recommend"
    MARK_COMPLETED = "mark_completed"
    REOPEN = "reopen"
    ARCHIVE = "archive"


_TRANSITIONS: dict[tuple[RunStatus, RunEvent], RunStatus] = {
    (RunStatus.DRAFT, RunEvent.VALIDATE_DEFINITION_PASS): RunStatus.DESIGN_SPACE_VALIDATED,
    (RunStatus.DRAFT, RunEvent.VALIDATE_DEFINITION_FAIL): RunStatus.DRAFT,
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.EDIT_DEFINITION): RunStatus.DRAFT,
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.GENERATE_INITIAL_DESIGN): RunStatus.RECOMMENDATIONS_PENDING,
    (RunStatus.DESIGN_SPACE_VALIDATED, RunEvent.ARCHIVE): RunStatus.ARCHIVED,
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.ALL_RUNS_TERMINAL): RunStatus.AWAITING_MEASUREMENTS,
    (RunStatus.RECOMMENDATIONS_PENDING, RunEvent.ABORT_ROUND): RunStatus.ROUND_CLOSED,
    (RunStatus.AWAITING_MEASUREMENTS, RunEvent.CLOSE_ROUND): RunStatus.ROUND_CLOSED,
    (RunStatus.AWAITING_MEASUREMENTS, RunEvent.ABORT_ROUND): RunStatus.ROUND_CLOSED,
    (RunStatus.ROUND_CLOSED, RunEvent.RECOMMEND): RunStatus.RECOMMENDATIONS_PENDING,
    (RunStatus.ROUND_CLOSED, RunEvent.MARK_COMPLETED): RunStatus.COMPLETED,
    (RunStatus.ROUND_CLOSED, RunEvent.ARCHIVE): RunStatus.ARCHIVED,
    (RunStatus.COMPLETED, RunEvent.REOPEN): RunStatus.ROUND_CLOSED,
    (RunStatus.COMPLETED, RunEvent.ARCHIVE): RunStatus.ARCHIVED,
}
"""The permitted ``(status, event) -> status`` transitions (§3.2)."""


def can_transition(current: RunStatus, event: RunEvent) -> bool:
    """Return whether ``event`` is permitted in the ``current`` state.

    Args:
        current: The run's current status.
        event: The event being attempted.

    Returns:
        ``True`` if the transition is defined by §3.2, else ``False``.
    """
    return (current, event) in _TRANSITIONS


def next_status(current: RunStatus, event: RunEvent) -> RunStatus:
    """Return the status reached by applying ``event`` to ``current``.

    Args:
        current: The run's current status.
        event: The event being applied.

    Returns:
        The resulting :class:`RunStatus`.

    Raises:
        StateTransitionError: If the transition is not permitted by §3.2.
    """
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError as exc:
        raise StateTransitionError(
            f"Event {event.value!r} is not permitted from state "
            f"{current.value!r}."
        ) from exc


# Measurement supersede chains (§2.12) --------------------------------------


def active_measurements(measurements: list[Measurement]) -> list[Measurement]:
    """Return the active reading for each ``(experimentRunId, outputId)`` (§2.12).

    The active reading is the head of the supersede chain (not superseded by any
    other measurement) provided it is ``Valid``.

    Args:
        measurements: All measurements to consider.

    Returns:
        The active measurements, in input order.
    """
    superseded_ids = {
        m.supersedes_measurement_id
        for m in measurements
        if m.supersedes_measurement_id is not None
    }
    return [
        m
        for m in measurements
        if m.id not in superseded_ids and m.status is MeasurementStatus.VALID
    ]


def validate_supersede_chains(measurements: list[Measurement]) -> ValidationResult:
    """Check the integrity of every ``(experimentRunId, outputId)`` chain (§2.12).

    Each ``(experimentRunId, outputId)`` must form a single linear supersede
    chain: globally unique ids; per-key revisions ``1..n`` with no duplicates or
    gaps; every revision after the first superseding exactly the immediately
    preceding revision; no foreign or dangling pointers; no branch (two readings
    superseding the same one), no cycle, and exactly one head.

    Args:
        measurements: All measurements to consider.

    Returns:
        A :class:`ValidationResult`; any of the above violations is blocking.
    """
    issues: list[ValidationIssue] = []
    by_id = {m.id: m for m in measurements}

    id_counts = Counter(m.id for m in measurements)
    for measurement_id, count in id_counts.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_MEASUREMENT_ID",
                    message=f"Measurement id {measurement_id!r} appears {count} "
                    "times; ids must be unique.",
                    severity=Severity.BLOCKING,
                    related_entity_id=measurement_id,
                )
            )

    groups: dict[tuple[str, str], list[Measurement]] = {}
    for m in measurements:
        groups.setdefault((m.experiment_run_id, m.output_id), []).append(m)

    for m in measurements:
        target = m.supersedes_measurement_id
        if target is None:
            continue
        superseded = by_id.get(target)
        if superseded is None:
            issues.append(
                ValidationIssue(
                    code="SUPERSEDES_UNKNOWN",
                    message=f"Measurement {m.id!r} supersedes unknown measurement "
                    f"{target!r}.",
                    severity=Severity.BLOCKING,
                    related_entity_id=m.id,
                )
            )
        elif (superseded.experiment_run_id, superseded.output_id) != (
            m.experiment_run_id,
            m.output_id,
        ):
            issues.append(
                ValidationIssue(
                    code="SUPERSEDES_FOREIGN",
                    message=f"Measurement {m.id!r} supersedes a measurement from a "
                    "different (experimentRunId, outputId).",
                    severity=Severity.BLOCKING,
                    related_entity_id=m.id,
                )
            )

    superseded_ids = {
        m.supersedes_measurement_id
        for m in measurements
        if m.supersedes_measurement_id is not None
    }
    for key, group in groups.items():
        issues.extend(_validate_chain_group(key, group, superseded_ids))

    issues.extend(_detect_supersede_cycles(measurements, by_id))

    return ValidationResult(issues=tuple(issues))


def _validate_chain_group(
    key: tuple[str, str],
    group: list[Measurement],
    superseded_ids: set[str | None],
) -> list[ValidationIssue]:
    """Validate one ``(experimentRunId, outputId)`` group as a linear chain."""
    issues: list[ValidationIssue] = []

    heads = [m for m in group if m.id not in superseded_ids]
    if len(heads) > 1:
        issues.append(
            ValidationIssue(
                code="MULTIPLE_CHAIN_HEADS",
                message=f"({key[0]}, {key[1]}) has {len(heads)} active heads; "
                "expected exactly one supersede chain.",
                severity=Severity.BLOCKING,
            )
        )

    revision_counts = Counter(m.revision for m in group)
    for revision, count in revision_counts.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_REVISION",
                    message=f"({key[0]}, {key[1]}) has {count} measurements at "
                    f"revision {revision}; revisions must be unique.",
                    severity=Severity.BLOCKING,
                )
            )

    if sorted(m.revision for m in group) != list(range(1, len(group) + 1)):
        issues.append(
            ValidationIssue(
                code="REVISION_NOT_CONTIGUOUS",
                message=f"({key[0]}, {key[1]}) revisions must be contiguous from 1 "
                f"to {len(group)}.",
                severity=Severity.BLOCKING,
            )
        )

    target_counts = Counter(
        m.supersedes_measurement_id
        for m in group
        if m.supersedes_measurement_id is not None
    )
    for target, count in target_counts.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    code="SUPERSEDE_BRANCH",
                    message=f"Measurement {target!r} is superseded by {count} "
                    "measurements; a chain may not branch.",
                    severity=Severity.BLOCKING,
                    related_entity_id=target,
                )
            )

    by_revision: dict[int, Measurement] = {}
    for m in group:
        by_revision.setdefault(m.revision, m)
    for m in group:
        if m.revision == 1:
            continue
        predecessor = by_revision.get(m.revision - 1)
        if predecessor is None or m.supersedes_measurement_id != predecessor.id:
            issues.append(
                ValidationIssue(
                    code="SUPERSEDES_NOT_PREDECESSOR",
                    message=f"Measurement {m.id!r} (revision {m.revision}) must "
                    "supersede the immediately preceding revision.",
                    severity=Severity.BLOCKING,
                    related_entity_id=m.id,
                )
            )

    return issues


def _detect_supersede_cycles(
    measurements: list[Measurement], by_id: dict[str, Measurement]
) -> list[ValidationIssue]:
    """Flag any measurement that lies on a supersede cycle."""
    issues: list[ValidationIssue] = []
    reported: set[str] = set()
    for start in measurements:
        seen: set[str] = set()
        node: Measurement | None = start
        while node is not None:
            if node.id in seen:
                if node.id not in reported:
                    reported.add(node.id)
                    issues.append(
                        ValidationIssue(
                            code="SUPERSEDE_CYCLE",
                            message=f"Measurement {node.id!r} lies on a supersede "
                            "cycle.",
                            severity=Severity.BLOCKING,
                            related_entity_id=node.id,
                        )
                    )
                break
            seen.add(node.id)
            target = node.supersedes_measurement_id
            node = by_id.get(target) if target is not None else None
    return issues


__all__ = [
    "RunEvent",
    "Severity",
    "StateTransitionError",
    "TOLERANCE",
    "ValidationIssue",
    "ValidationResult",
    "active_measurements",
    "can_transition",
    "next_status",
    "validate_candidates",
    "validate_definition",
    "validate_supersede_chains",
]
