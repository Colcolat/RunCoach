from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, status

from src.database import check_connection

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> dict:
    """Report measured component state.

    A health check that always answers "healthy" tells the operator nothing, so
    the database entry comes from an actual query.
    """
    database_ok = check_connection()
    return {
        "status": "healthy" if database_ok else "degraded",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "database": "connected" if database_ok else "unavailable",
        },
    }
