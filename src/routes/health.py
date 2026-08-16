from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status

from src.config import get_settings
from src.database import check_connection
from src.dependencies import get_gemini, get_telegram
from src.services.gemini_service import GeminiService
from src.services.telegram_service import TelegramService

router = APIRouter(tags=["health"])

VERSION = "0.2.0"


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check(
    gemini: GeminiService = Depends(get_gemini),
    telegram: TelegramService = Depends(get_telegram),
) -> dict:
    """Report measured component state.

    A health check that always answers "healthy" tells the operator nothing, so
    the database entry comes from an actual query.

    The model and bot entries report configuration, not reachability: probing
    the model would spend a request from a capped daily quota on every health
    check, and probing Telegram would do it on someone else's rate limit.

    Neither is part of `status`. A missing key or bot degrades the coach but
    leaves the application serving, and a health check that calls that
    "unhealthy" gets a restart loop instead of an operator reading the reason.
    """
    database_ok = check_connection()
    settings = get_settings()
    return {
        "status": "healthy" if database_ok else "degraded",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "components": {
            "database": "connected" if database_ok else "unavailable",
            "gemini": "configured" if gemini.enabled else "not_configured",
            "gemini_model": gemini.model,
            "telegram": "configured" if telegram.enabled else "not_configured",
            "reminders": "on" if settings.reminders_enabled else "off",
        },
    }
