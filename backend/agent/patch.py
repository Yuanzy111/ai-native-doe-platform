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
from typing import Any

from backend.agent.contract import (
    AddObjectiveOp,
    AddParameterOp,
    AgentObjectiveInput,
    AgentObjectivePatch,
    AgentParameterInput,
    AgentParameterPatch,
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
        existing = next((param for param in parameters if param.id == op.id), None)
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot update unknown parameter {op.id!r}."
            )
        merged = _merge_parameter(existing, op.patch)
        return [merged if param.id == op.id else param for param in parameters]
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


def _merge_parameter(
    existing: ParameterSpec, patch: AgentParameterPatch
) -> ParameterSpec:
    """Merge a partial patch onto an existing parameter (§partial-update).

    A field absent from the patch keeps the existing value; ``unit``/
    ``description`` present but ``null`` clear it (told apart via
    ``model_fields_set``). The parameter ``type`` may change, in which case the
    new type's fields must be supplied by the patch. The merged dict is built
    into the real domain spec, so any illegal result (``lower >= upper``, empty
    categorical set, blank name) raises a pydantic ``ValidationError`` the caller
    maps to ``AGENT_INVALID_ACTION``.
    """
    was_set = patch.model_fields_set
    new_type = patch.type if "type" in was_set else existing.type

    base = {
        "id": existing.id,
        "name": patch.name if "name" in was_set else existing.name,
        "unit": patch.unit if "unit" in was_set else existing.unit,
        "description": (
            patch.description if "description" in was_set else existing.description
        ),
    }

    if new_type == "Continuous":
        lower = (
            patch.lower_bound
            if "lowerBound" in was_set or "lower_bound" in was_set
            else getattr(getattr(existing, "bounds", None), "lower", None)
        )
        upper = (
            patch.upper_bound
            if "upperBound" in was_set or "upper_bound" in was_set
            else getattr(getattr(existing, "bounds", None), "upper", None)
        )
        if lower is None or upper is None:
            raise AgentActionRejectedError(
                "Changing a parameter to Continuous requires lowerBound and "
                "upperBound."
            )
        return ContinuousParameterSpec(**base, bounds=Bounds(lower=lower, upper=upper))

    # Discrete / Categorical both need a values list.
    values = (
        patch.values
        if "values" in was_set
        else list(getattr(existing, "values", []) or [])
    )
    if not values:
        raise AgentActionRejectedError(
            f"Changing a parameter to {new_type} requires a non-empty values list."
        )
    if new_type == "Discrete":
        return DiscreteParameterSpec(**base, values=[float(v) for v in values])
    return CategoricalParameterSpec(**base, values=[str(v) for v in values])


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
        existing = next(
            (record for record in objectives if record.target_id == op.id), None
        )
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot update unknown objective {op.id!r}."
            )
        merged = _merge_objective(existing, op.patch)
        return [merged if record.target_id == op.id else record for record in objectives]
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


def _merge_objective(
    existing: _ObjectiveRecord, patch: AgentObjectivePatch
) -> _ObjectiveRecord:
    """Merge a partial patch onto an existing objective (§partial-update).

    Absent fields keep the current value; ``unit``/``description`` present but
    ``null`` clear it (told apart via ``model_fields_set``).
    """
    was_set = patch.model_fields_set
    return _ObjectiveRecord(
        output_id=existing.output_id,
        target_id=existing.target_id,
        name=patch.name if "name" in was_set else existing.name,
        direction=patch.direction if "direction" in was_set else existing.direction,
        unit=patch.unit if "unit" in was_set else existing.unit,
        description=(
            patch.description if "description" in was_set else existing.description
        ),
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


# Effect preview ------------------------------------------------------------


def _fmt(value: Any) -> str:
    """Render a field value for the preview; ``None`` becomes an empty string.

    An empty string is what the frontend shows as ``(empty)``. Numbers drop a
    trailing ``.0`` so ``20.0`` reads as ``20``; lists join with ``, ``.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(item) for item in value)
    return str(value)


def _parameter_fields(param: ParameterSpec) -> dict[str, str]:
    """The preview field map for a parameter (final persisted values)."""
    fields = {
        "name": _fmt(param.name),
        "type": _fmt(param.type),
        "unit": _fmt(param.unit),
        "description": _fmt(param.description),
    }
    if isinstance(param, ContinuousParameterSpec):
        fields["lowerBound"] = _fmt(param.bounds.lower)
        fields["upperBound"] = _fmt(param.bounds.upper)
    else:
        fields["values"] = _fmt(list(param.values))
    return fields


def _objective_fields(record: _ObjectiveRecord) -> dict[str, str]:
    """The preview field map for an objective (final persisted values)."""
    return {
        "name": _fmt(record.name),
        "direction": _fmt(record.direction),
        "unit": _fmt(record.unit),
        "description": _fmt(record.description),
    }


def _changed_fields(
    before: dict[str, str] | None, after: dict[str, str] | None
) -> list[dict[str, Any]]:
    """Field-level diff between two preview maps (add/update/delete aware).

    For an update, only fields whose value actually changed are emitted. For an
    add (``before is None``) or delete (``after is None``) every non-empty field
    on the present side is emitted, so a delete preview lists the entity's real
    values rather than only its id.
    """
    keys: list[str] = []
    for source in (before, after):
        if source is not None:
            for key in source:
                if key not in keys:
                    keys.append(key)
    changed: list[dict[str, Any]] = []
    for key in keys:
        b = before.get(key) if before is not None else None
        a = after.get(key) if after is not None else None
        if before is not None and after is not None and b == a:
            continue
        if before is None and not a:
            continue
        if after is None and not b:
            continue
        changed.append({"field": key, "before": b, "after": a})
    return changed


def build_effect_preview(
    revision: CampaignDefinitionRevision, op: PatchOp
) -> dict[str, Any]:
    """Dry-run ``op`` against ``revision`` and return the structured preview.

    The returned dict matches :class:`~backend.agent.contract.EffectPreview`
    (camelCase keys) and reflects the *final persisted* values after approval,
    computed from the same merge/apply logic the real save uses — never from
    frontend state. Raises the same rejections as :func:`apply_patch` (unknown
    id, illegal value) so a bad target is refused before a proposal is stored.
    """
    params_before = {param.id: param for param in revision.parameters}
    objectives_before = {rec.target_id: rec for rec in _extract_objectives(revision)}

    if isinstance(op, AddParameterOp):
        spec = _to_parameter_spec(op.parameter, _new_id())
        return {
            "entityType": "parameter",
            "entityId": None,
            "operation": "add",
            "entityName": spec.name,
            "changedFields": _changed_fields(None, _parameter_fields(spec)),
        }
    if isinstance(op, UpdateParameterOp):
        existing = params_before.get(op.id)
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot update unknown parameter {op.id!r}."
            )
        merged = _merge_parameter(existing, op.patch)
        return {
            "entityType": "parameter",
            "entityId": op.id,
            "operation": "update",
            "entityName": merged.name,
            "changedFields": _changed_fields(
                _parameter_fields(existing), _parameter_fields(merged)
            ),
        }
    if isinstance(op, DeleteParameterOp):
        existing = params_before.get(op.id)
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot delete unknown parameter {op.id!r}."
            )
        return {
            "entityType": "parameter",
            "entityId": op.id,
            "operation": "delete",
            "entityName": existing.name,
            "changedFields": _changed_fields(_parameter_fields(existing), None),
        }
    if isinstance(op, AddObjectiveOp):
        record = _to_objective_record(op.objective, _new_id(), _new_id())
        return {
            "entityType": "objective",
            "entityId": None,
            "operation": "add",
            "entityName": record.name,
            "changedFields": _changed_fields(None, _objective_fields(record)),
        }
    if isinstance(op, UpdateObjectiveOp):
        existing = objectives_before.get(op.id)
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot update unknown objective {op.id!r}."
            )
        merged = _merge_objective(existing, op.patch)
        return {
            "entityType": "objective",
            "entityId": op.id,
            "operation": "update",
            "entityName": merged.name,
            "changedFields": _changed_fields(
                _objective_fields(existing), _objective_fields(merged)
            ),
        }
    if isinstance(op, DeleteObjectiveOp):
        existing = objectives_before.get(op.id)
        if existing is None:
            raise AgentActionRejectedError(
                f"Cannot delete unknown objective {op.id!r}."
            )
        return {
            "entityType": "objective",
            "entityId": op.id,
            "operation": "delete",
            "entityName": existing.name,
            "changedFields": _changed_fields(_objective_fields(existing), None),
        }
    if isinstance(op, SetNoConstraintOp):
        return {
            "entityType": "constraint",
            "entityId": None,
            "operation": "set",
            "entityName": None,
            "changedFields": [
                {"field": "constraint", "before": None, "after": "No fixed-sum constraint"}
            ],
        }
    if isinstance(op, SetFixedSumConstraintOp):
        constraint = _build_fixed_sum(op, list(revision.parameters))
        summary = f"{' + '.join(constraint.parameter_ids)} = {_fmt(constraint.rhs)}"
        return {
            "entityType": "constraint",
            "entityId": None,
            "operation": "set",
            "entityName": None,
            "changedFields": [
                {"field": "fixedSum", "before": None, "after": summary}
            ],
        }
    raise AgentActionRejectedError(  # pragma: no cover - union is exhaustive
        f"Unsupported patch op {op!r}."
    )


__all__ = ["PatchResult", "apply_patch", "build_effect_preview"]
