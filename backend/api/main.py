"""Runnable ASGI entry point for the API (architecture v0.2, §6/§7).

This module is the deployment seam: it resolves the SQLite file path from the
environment, ensures its parent directory exists, injects the *real* BayBE
adapter, and hands the assembled application to an ASGI server. It never holds
a long-lived SQLite connection at module scope — the file path is all that is
kept; connections are opened per request by the dependency layer.

Run with::

    uvicorn backend.api.main:build_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from backend.adapters.baybe import BayBEAdapter
from backend.api.app import create_app

_DEFAULT_DB_PATH = "data/doe.db"
_DB_PATH_ENV = "DOE_DB_PATH"


def _resolve_db_path() -> str:
    """Return the configured SQLite file path, creating its parent directory.

    The path comes from ``DOE_DB_PATH`` and defaults to ``data/doe.db``. Only
    the parent directory is created here; the schema is initialized when the
    application is built.
    """
    db_path = os.environ.get(_DB_PATH_ENV, _DEFAULT_DB_PATH)
    parent = Path(db_path).expanduser().resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    return db_path


def build_app() -> FastAPI:
    """Build the production application wired to real BayBE and the file DB.

    Intended for ``uvicorn ... --factory``: it is called once at startup and
    returns a ready-to-serve application without leaving a connection open.
    """
    return create_app(db_path=_resolve_db_path(), adapter=BayBEAdapter())


__all__ = ["build_app"]
