"""Request-scoped wiring for the API (architecture v0.2, §6/§7).

Each request opens its *own* SQLite connection, uses it for the whole request,
and closes it when the request ends — there is no module-level or app-level
shared connection, so nothing is shared across threads. The database path and
the optimizer adapter are read from ``app.state`` (populated by the application
factory), which is what makes both injectable and replaceable in tests.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, Request

from backend.api.errors import ApiError
from backend.api.query import RunQueryService
from backend.application import ApplicationService
from backend.application.adapter import OptimizerAdapter
from backend.persistence import SqliteRepository


def get_repository(request: Request) -> Iterator[SqliteRepository]:
    """Yield a fresh, request-scoped repository and close it afterwards.

    The connection is created here (never at import time) against the injected
    ``db_path`` and is torn down in the ``finally`` so it cannot outlive the
    request or be shared with another thread.
    """
    repository = SqliteRepository.connect(request.app.state.db_path)
    try:
        yield repository
    finally:
        repository.close()


def get_adapter(request: Request) -> OptimizerAdapter | None:
    """Return the injected optimizer adapter, if any."""
    return request.app.state.adapter


def get_service(
    repository: SqliteRepository = Depends(get_repository),
    adapter: OptimizerAdapter | None = Depends(get_adapter),
) -> ApplicationService:
    """Build the application service over the request-scoped repository."""
    return ApplicationService(repository, adapter=adapter)


def get_query_service(
    repository: SqliteRepository = Depends(get_repository),
) -> RunQueryService:
    """Build the read-only query service over the request-scoped repository.

    Because it depends on the same ``get_repository`` callable, FastAPI reuses
    the one connection already opened for this request rather than opening a
    second one.
    """
    return RunQueryService(repository)


def get_actor(x_actor_id: str | None = Header(default=None)) -> str:
    """Resolve the acting identity from the ``X-Actor-Id`` header.

    Only presence is checked — there is deliberately no login, user store, JWT,
    or permission model in this pass.
    """
    if x_actor_id is None or not x_actor_id.strip():
        raise ApiError(
            422,
            "MISSING_ACTOR",
            "The X-Actor-Id header is required and must not be empty.",
        )
    return x_actor_id.strip()


__all__ = [
    "get_actor",
    "get_adapter",
    "get_query_service",
    "get_repository",
    "get_service",
]
