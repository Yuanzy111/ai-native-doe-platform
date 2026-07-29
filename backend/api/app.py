"""The FastAPI application factory (architecture v0.2, §6).

``create_app`` wires the routers, the uniform error contract, and the injectable
database path and optimizer adapter. No SQLite connection is opened at import
time: the schema is initialized once here (opening and immediately closing a
throwaway connection), and every request later opens its own short-lived
connection via the request-scoped dependency.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.api.errors import register_exception_handlers
from backend.api.routers import campaign_runs, health
from backend.application.adapter import OptimizerAdapter
from backend.persistence import SqliteRepository


def create_app(
    *,
    db_path: str,
    adapter: OptimizerAdapter | None = None,
) -> FastAPI:
    """Build a configured application.

    Args:
        db_path: The SQLite file path (or ``":memory:"``). It is stored on
            ``app.state`` and read per request; nothing is opened at import time.
        adapter: The optimizer adapter used for recommendation legs. Injectable
            so tests can swap in a fake or the real BayBE adapter.

    Returns:
        A ready-to-serve :class:`FastAPI` application.
    """
    app = FastAPI(title="Industrial Optimization API", version="v1")
    app.state.db_path = db_path
    app.state.adapter = adapter

    _initialize_schema(db_path)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(campaign_runs.router)
    return app


def _initialize_schema(db_path: str) -> None:
    """Ensure the database file and schema exist without holding a connection."""
    repository = SqliteRepository.connect(db_path)
    repository.close()


__all__ = ["create_app"]
