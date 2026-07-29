"""Liveness endpoint."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Return a static liveness marker."""
    return {"status": "ok"}


__all__ = ["router"]
