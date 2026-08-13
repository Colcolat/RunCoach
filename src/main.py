from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.config import get_settings
from src.database import check_connection
from src.routes import chat, health, voice

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not check_connection():
        # Surfaced rather than fatal: /health should be reachable to report the
        # problem, which is precisely when an operator needs it most.
        logger.error("Database unreachable at startup")

    if not settings.gemini_enabled:
        logger.warning("GOOGLE_API_KEY not set - the coach will reply in degraded mode")

    logger.info("RunCoach started")
    yield
    logger.info("RunCoach stopped")


app = FastAPI(
    title="RunCoach",
    description="Conversational running coach for 5K, 10K, 21K and marathon training",
    version=health.VERSION,
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(voice.router)
