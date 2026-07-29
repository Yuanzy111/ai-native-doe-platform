"""Application layer: the state-change service that owns every run mutation (§3, §4.1)."""

from backend.application.service import (
    ApplicationService,
    DesignSpaceSaveResult,
    DesignSpaceUpdate,
    EntityNotFoundError,
    ServiceError,
)

__all__ = [
    "ApplicationService",
    "DesignSpaceSaveResult",
    "DesignSpaceUpdate",
    "EntityNotFoundError",
    "ServiceError",
]
