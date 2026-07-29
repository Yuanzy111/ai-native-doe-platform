"""Startup test for the runnable entry point (architecture v0.2, §6/§7).

Confirms ``build_app`` honours ``DOE_DB_PATH``, creates the database file, and
serves a healthy app — without asserting anything about BayBE itself.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.main import build_app


def test_build_app_creates_db_and_serves_health(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "nested" / "doe.db"
    monkeypatch.setenv("DOE_DB_PATH", str(db_path))

    app = build_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert Path(db_path).exists()
