"""Apply a structured agent patch to a design-space revision (§三/§四).

These are pure functions: given the run's current pinned revision and one
:class:`~backend.agent.contract.PatchOp`, produce the *full* new design-space
lists that :meth:`ApplicationService.save_design_space` expects. Nothing here
touches the database or the optimizer.

The objective/constraint recomputation mirrors the frontend mapper
(``objectiveArtifacts`` / ``constraintArtifacts`` in ``frontend/src/api/mapper.ts``)
so an agent-authored change round-trips to the same shapes the manual editor
produces: one output+target per objective, ``Single``/``Pareto`` policy by count,
``qLogEI``/``qLogNEHVI`` acquisition, and the standard fixed-sum LinearEquality.

Ids for *new* entities are minted here (never taken from the model); an
``update``/``delete`` op that references an unknown id raises
:class:`AgentActionRejectedError`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.agent.contract import (
    AddObjectiveOp,
    AddParameterOp,
    AgentObjectiveInput,
    AgentParameterInput,
    DeleteObjectiveOp,
    DeleteParameterOp,
    PatchOp,
    SetFixedSumConstraintOp,
    SetNoConstraintOp,
    UpdateObjectiveOp,
    UpdateParameterOp,
)
from backend.agent.errors import AgentActionRejectedError
from backend.domain.models import (
    Bounds,
    CampaignDefinitionRevision,
    CategoricalParameterSpec,
    ConstraintSpec,
    ContinuousParameterSpec,
    Direction,
    DiscreteParameterSpec,
    LinearEqualityConstraintSpec,
    ObjectivePolicy,
    OutputSpec,
    ParameterSpec,
    ParetoObjectivePolicy,
    SingleObjectivePolicy,
    TargetSpec,
)


@dataclass(frozen=True)
class PatchResult:
    """The full design space produced by applying one op to a revision.

    Everything :class:`DesignSpaceUpdate` needs except the agent-immutable policy
    base; ``acquisition_function`` is derived from the objective count so the
    caller can keep the rest of the policy fixed while honoring the invariant the
    manual editor enforces.
    """

    parameters: list[ParameterSpec]
    outputs: list[OutputSpec]
    targets: list[TargetSpec]
    objective_policy: ObjectivePolicy
    constraints: list[ConstraintSpec]
    constraints_confirmed: bool
    acquisition_function: str


@dataclass
class _ObjectiveRecord:
    """The UI-level objective: one output paired with its target."""

    output_id: str
    target_id: str
    name: str
    direction: Direction
    unit: str | None
    description: str | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def apply_patch(revision: CampaignDefinitionRevision, op: PatchOp) -> PatchResult:
    """Apply ``op`` to ``revision`` and return the full new design space.

    Raises:
        AgentActionRejectedError: If an ``update``/``delete`` op references an
            unknown id, or the result would violate a structural invariant (e.g.
            the last objective removed, or a fixed-sum over fewer than two
            parameters).
    """
    parameters = list(revision.parameters)
    objectives = _extract_objectives(revision)
    constraints = list(revision.constraints)
    constraints_confirmed = revision.constraints_confirmed

    if isinstance(op, (AddParameterOp, UpdateParameterOp, DeleteParameterOp)):
        parameters = _apply_parameter_op(parameters, op)
    elif isinstance(op, (AddObjectiveOp, UpdateObjectiveOp, DeleteObjectiveOp)):
        objectives = _apply_objective_op(objectives, op)
    elif isinstance(op, SetNoConstraintOp):
        constraints, constraints_confirmed = [], True
    elif isinstance(op, SetFixedSumConstraintOp):
        constraints = [_build_fixed_sum(op, parameters)]
        constraints_confirmed = True
    else:  # pragma: no cover - the discriminated union forbids other tags
        raise AgentActionRejectedError(f"Unsupported patch op {op!r}.")

    outputs, targets, objective_policy, acquisition = _objective_artifacts(objectives)
    return PatchResult(
        parameters=parameters,
        outputs=outputs,
        targets=targets,
        objective_policy=objective_policy,
        constraints=constraints,
        constraints_confirmed=constraints_confirmed,
        acquisition_function=acquisition,
    )


# Parameters ----------------------------------------------------------------


def _apply_parameter_op(
    parameters: list[ParameterSpec],
    op: AddParameterOp | UpdateParameterOp | DeleteParameterOp,
) -> list[ParameterSpec]:
    if isinstance(op, AddParameterOp):
        return [*parameters, _to_parameter_spec(op.parameter, _new_id())]
    if isinstance(op, UpdateParameterOp):
        if not any(param.id == op.id for param in parameters):
            raise AgentActionRejectedError(
                f"Cannot update unknown parameter {op.id!r}."
            )
        return [
            _to_parameter_spec(op.parameter, param.id) if param.id == op.id else param
            for param in parameters
        ]
    # DeleteParameterOp
    if not any(param.id == op.id for param in parameters):
        raise AgentActionRejectedError(f"Cannot delete unknown parameter {op.id!r}.")
    remaining = [param for param in parameters if param.id != op.id]
    if not remaining:
        raise AgentActionRejectedError(
            "At least one parameter is required; cannot delete the last one."
        )
    return remaining


def _to_parameter_spec(inp: AgentParameterInput, param_id: str) -> ParameterSpec:
    base = {
        "id": param_id,
        "name": inp.name,
        "unit": inp.unit,
        "description": inp.description,
    }
    if inp.type == "Continuous":
        return ContinuousParameterSpec(
            **base, bounds=Bounds(lower=inp.lower_bound, upper=inp.upper_bound)
        )
    if inp.type == "Discrete":
        return DiscreteParameterSpec(**base, values=inp.values)
    return CategoricalParameterSpec(**base, values=inp.values)


# Objectives ----------------------------------------------------------------


def _extract_objectives(revision: CampaignDefinitionRevision) -> list[_ObjectiveRecord]:
    outputs_by_id = {output.id: output for output in revision.outputs}
    records: list[_ObjectiveRecord] = []
    for target in revision.targets:
        output = outputs_by_id.get(target.output_id)
        records.append(
            _ObjectiveRecord(
                output_id=target.output_id,
                target_id=target.id,
                name=output.name if output is not None else target.id,
                direction=target.direction,
                unit=output.unit if output is not None else None,
                description=output.description if output is not None else None,
            )
        )
    return records


def _apply_objective_op(
    objectives: list[_ObjectiveRecord],
    op: AddObjectiveOp | UpdateObjectiveOp | DeleteObjectiveOp,
) -> list[_ObjectiveRecord]:
    if isinstance(op, AddObjectiveOp):
        return [*objectives, _to_objective_record(op.objective, _new_id(), _new_id())]
    if isinstance(op, UpdateObjectiveOp):
        if not any(record.target_id == op.id for record in objectives):
            raise AgentActionRejectedError(
                f"Cannot update unknown objective {op.id!r}."
            )
        return [
            _to_objective_record(op.objective, record.output_id, record.target_id)
            if record.target_id == op.id
            else record
            for record in objectives
        ]
    # DeleteObjectiveOp
    if not any(record.target_id == op.id for record in objectives):
        raise AgentActionRejectedError(f"Cannot delete unknown objective {op.id!r}.")
    remaining = [record for record in objectives if record.target_id != op.id]
    if not remaining:
        raise AgentActionRejectedError(
            "At least one objective is required; cannot delete the last one."
        )
    return remaining


def _to_objective_record(
    inp: AgentObjectiveInput, output_id: str, target_id: str
) -> _ObjectiveRecord:
    return _ObjectiveRecord(
        output_id=output_id,
        target_id=target_id,
        name=inp.name,
        direction=inp.direction,
        unit=inp.unit,
        description=inp.description,
    )


def _objective_artifacts(
    objectives: list[_ObjectiveRecord],
) -> tuple[list[OutputSpec], list[TargetSpec], ObjectivePolicy, str]:
    if not objectives:
        raise AgentActionRejectedError("At least one objective is required.")
    outputs = [
        OutputSpec(
            id=record.output_id,
            name=record.name,
            unit=record.unit,
            description=record.description,
        )
        for record in objectives
    ]
    targets = [
        TargetSpec(
            id=record.target_id,
            output_id=record.output_id,
            direction=record.direction,
        )
        for record in objectives
    ]
    if len(objectives) == 1:
        return outputs, targets, SingleObjectivePolicy(target_id=objectives[0].target_id), "qLogEI"
    return (
        outputs,
        targets,
        ParetoObjectivePolicy(target_ids=[record.target_id for record in objectives]),
        "qLogNEHVI",
    )


# Constraints ---------------------------------------------------------------


def _build_fixed_sum(
    op: SetFixedSumConstraintOp, parameters: list[ParameterSpec]
) -> LinearEqualityConstraintSpec:
    parameter_ids = op.parameter_ids or _default_fixed_sum_ids(parameters)
    known = {param.id for param in parameters}
    unknown = [pid for pid in parameter_ids if pid not in known]
    if unknown:
        raise AgentActionRejectedError(
            f"Fixed-sum constraint references unknown parameter(s): {unknown}."
        )
    if len(parameter_ids) < 2:
        raise AgentActionRejectedError(
            "A fixed-sum constraint needs at least two parameters."
        )
    return LinearEqualityConstraintSpec(
        id=_new_id(),
        resolved_at=_now(),
        parameter_ids=parameter_ids,
        coefficients=[1.0] * len(parameter_ids),
        rhs=op.rhs,
    )


def _default_fixed_sum_ids(parameters: list[ParameterSpec]) -> list[str]:
    resin = next(
        (param for param in parameters if "resin" in param.name.lower()), None
    )
    hardener = next(
        (param for param in parameters if "hardener" in param.name.lower()), None
    )
    if resin is not None and hardener is not None:
        return [resin.id, hardener.id]
    if len(parameters) >= 2:
        return [parameters[0].id, parameters[1].id]
    raise AgentActionRejectedError(
        "A fixed-sum constraint needs at least two parameters."
    )


__all__ = ["PatchResult", "apply_patch"]
