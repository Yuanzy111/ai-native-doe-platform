"""Campaign-run endpoints (architecture v0.2, §6).

The routes translate a request DTO into domain objects, delegate every write to
:class:`~backend.application.ApplicationService` (which owns the transaction),
and every read to :class:`~backend.api.query.RunQueryService`. The router itself
never touches the database and never assembles cross-aggregate state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, status

from backend.api.dependencies import get_actor, get_query_service, get_service
from backend.api.query import RunQueryService
from backend.api.schemas import CreateCampaignRunRequest, SaveDesignSpaceRequest
from backend.application import ApplicationService, DesignSpaceUpdate
from backend.domain.models import (
    CampaignDefinition,
    CampaignDefinitionRevision,
    CampaignRun,
    OptimizationPolicy,
    RunStatus,
)

router = APIRouter(prefix="/api/v1/campaign-runs", tags=["campaign-runs"])


def _now() -> datetime:
    """Return the current timezone-aware timestamp."""
    return datetime.now(timezone.utc)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_campaign_run(
    body: CreateCampaignRunRequest,
    actor: str = Depends(get_actor),
    service: ApplicationService = Depends(get_service),
    query: RunQueryService = Depends(get_query_service),
) -> dict[str, Any]:
    """Create a campaign definition, its first revision, and a Draft run.

    Every aggregate-root id, ``revisionNumber``, timestamp, the initial status,
    ``budgetUsed`` and ``round`` are assigned here; the request only carried the
    fields a user supplies.
    """
    now = _now()
    definition_id = str(uuid.uuid4())
    revision_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    definition = CampaignDefinition(
        id=definition_id,
        name=body.name,
        goal=body.goal,
        head_revision_id=revision_id,
        created_at=now,
        created_by=actor,
        updated_at=now,
    )
    revision = CampaignDefinitionRevision(
        id=revision_id,
        campaign_definition_id=definition_id,
        revision_number=1,
        parent_revision_id=None,
        parameters=body.parameters,
        outputs=body.outputs,
        targets=body.targets,
        objective_policy=body.objective_policy,
        constraints=body.constraints,
        constraints_confirmed=body.constraints_confirmed,
        constraints_confirmed_at=now if body.constraints_confirmed else None,
        created_at=now,
        created_by=actor,
    )
    policy = OptimizationPolicy(
        id=str(uuid.uuid4()),
        backend_name=body.optimization_policy.backend_name,
        batch_size=body.optimization_policy.batch_size,
        seed_policy=body.optimization_policy.seed_policy,
        seed_value=body.optimization_policy.seed_value,
        strategy_config=body.optimization_policy.strategy_config,
    )
    run = CampaignRun(
        id=run_id,
        campaign_definition_id=definition_id,
        definition_revision_id=revision_id,
        status=RunStatus.DRAFT,
        optimization_policy=policy,
        round=0,
        budget_total=body.budget_total,
        budget_used=0,
        created_at=now,
        updated_at=now,
        created_by=actor,
    )

    service.create_campaign_with_run(definition, revision, run)
    return query.run_view(run_id)


@router.post("/{run_id}/validate")
def validate_design_space(
    run_id: str,
    actor: str = Depends(get_actor),
    service: ApplicationService = Depends(get_service),
    query: RunQueryService = Depends(get_query_service),
) -> dict[str, Any]:
    """Run the deterministic design-space validation and report the outcome.

    A clean result advances the run to ``DesignSpaceValidated``; a blocking
    result returns the issues and leaves the run in ``Draft``.
    """
    result = service.validate_design_space(run_id, actor)
    return {
        "validationResult": {
            "ok": result.ok,
            **result.model_dump(mode="json", by_alias=True),
        },
        "campaignRun": query.run_view(run_id)["campaignRun"],
    }


@router.put("/{run_id}/design-space")
def save_design_space(
    run_id: str,
    body: SaveDesignSpaceRequest,
    actor: str = Depends(get_actor),
    service: ApplicationService = Depends(get_service),
    query: RunQueryService = Depends(get_query_service),
) -> dict[str, Any]:
    """Save an edited design space and/or policy for an editable run.

    The service decides what actually changed (creating a new revision and/or
    minting a new policy id), drops a validated run back to ``Draft`` on any
    change, and treats an identical request as a no-op. The response reports the
    change flags alongside the full refreshed aggregate view.
    """
    update = DesignSpaceUpdate(
        parameters=body.parameters,
        outputs=body.outputs,
        targets=body.targets,
        objective_policy=body.objective_policy,
        constraints=body.constraints,
        constraints_confirmed=body.constraints_confirmed,
        backend_name=body.optimization_policy.backend_name,
        batch_size=body.optimization_policy.batch_size,
        seed_policy=body.optimization_policy.seed_policy,
        seed_value=body.optimization_policy.seed_value,
        strategy_config=body.optimization_policy.strategy_config,
    )
    result = service.save_design_space(run_id, actor, update)
    return {
        "changed": result.changed,
        "revisionChanged": result.revision_changed,
        "policyChanged": result.policy_changed,
        "view": query.run_view(run_id),
    }


@router.post("/{run_id}/initial-design", status_code=status.HTTP_201_CREATED)
def generate_initial_design(
    run_id: str,
    actor: str = Depends(get_actor),
    service: ApplicationService = Depends(get_service),
    query: RunQueryService = Depends(get_query_service),
) -> dict[str, Any]:
    """Generate the model-free first-round design with the real optimizer.

    Returns the updated run together with the batch, its open round, and the
    pending experiment runs produced in the one atomic transaction.
    """
    batch = service.generate_initial_design(run_id, actor)
    return query.initial_design_view(run_id, batch)


@router.get("/{run_id}")
def get_campaign_run(
    run_id: str,
    query: RunQueryService = Depends(get_query_service),
) -> dict[str, Any]:
    """Return the full aggregate view used to restore the Workbench page."""
    return query.run_view(run_id)


__all__ = ["router"]
