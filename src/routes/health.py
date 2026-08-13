from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status

from src.database import check_connection
from src.dependencies import get_gemini
from src.services.gemini_service import GeminiService

router = APIRouter(tags=["health"])

VERSION = "0.2.0"


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check(gemini: GeminiService = Depends(get_gemini)) -> dict:
    """Report measured component state.

    A health check that always answers "healthy" tells the operator nothing, so
    the database entry comes from an actual query.

    The model entry reports configuration, not reachability: probing it would
    spend a request from a capped daily quota on every health check.
    """
    database_ok = check_connection()
    return {
        "status": "healthy" if database_ok else "degraded",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "database": "connected" if database_ok else "unavailable",
            "gemini": "configured" if gemini.enabled else "not_configured",
            "gemini_model": gemini.model,
        },
    }
