"""The FastAPI application factory (architecture v0.2, §6).

``create_app`` wires the routers, the uniform error contract, and the injectable
database path and optimizer adapter. No SQLite connection is opened at import
time: the schema is initialized once here (opening and immediately closing a
throwaway connection), and every request later opens its own short-lived
connection via the request-scoped dependency.

The API accepts only a *file-backed* SQLite path. ``":memory:"`` is rejected
outright: because every request opens its own connection, a ``":memory:"`` path
would give each request a fresh, empty database, and sharing one global
in-memory connection across requests/threads is exactly the cross-thread
sharing this layer forbids. Domain and persistence unit tests may still use
``":memory:"`` directly against a single :class:`SqliteRepository`.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.agent.model import AgentModel
from backend.api.errors import register_exception_handlers
from backend.api.routers import agent, campaign_runs, health
from backend.application.adapter import OptimizerAdapter
from backend.persistence import SqliteRepository


def create_app(
    *,
    db_path: str,
    adapter: OptimizerAdapter | None = None,
    agent_model: AgentModel | None = None,
) -> FastAPI:
    """Build a configured application.

    Args:
        db_path: A file-backed SQLite path. It is stored on ``app.state`` and
            read per request; nothing is opened at import time. ``":memory:"``
            is rejected because per-request connections cannot share one
            in-memory database.
        adapter: The optimizer adapter used for recommendation legs. Injectable
            so tests can swap in a fake or the real BayBE adapter.
        agent_model: The LLM boundary for the agent. Injectable so tests supply a
            deterministic fake; ``None`` leaves the agent routes reporting
            ``AGENT_NOT_CONFIGURED`` while the rest of the API still serves.

    Returns:
        A ready-to-serve :class:`FastAPI` application.

    Raises:
        ValueError: If ``db_path`` is ``":memory:"``.
    """
    if db_path == ":memory:":
        raise ValueError(
            "The API requires a file-backed SQLite path; ':memory:' is not "
            "supported because each request opens its own connection and an "
            "in-memory database cannot be shared across them."
        )

    app = FastAPI(title="Industrial Optimization API", version="v1")
    app.state.db_path = db_path
    app.state.adapter = adapter
    app.state.agent_model = agent_model

    _initialize_schema(db_path)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(campaign_runs.router)
    app.include_router(agent.router)
    return app


def _initialize_schema(db_path: str) -> None:
    """Ensure the database file and schema exist without holding a connection."""
    repository = SqliteRepository.connect(db_path)
    repository.close()


__all__ = ["create_app"]
