from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.bot import handle_message, handle_start
from src.config import get_settings
from src.database import check_connection
from src.dependencies import get_coach, get_telegram
from src.scheduler import build_scheduler
from src.services.db_service import create_schema
from src.routes import chat, health, voice

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # httpx logs every request URL at INFO, and the Telegram bot token lives in
    # the path of every one of them. Polling means one request every few
    # seconds, so the token was written to the journal 536 times in a single
    # afternoon - readable by anyone in the adm group, and carried into any log
    # shipping or backup. Warnings and errors still come through.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    create_schema()

    if not check_connection():
        # Surfaced rather than fatal: /health should be reachable to report the
        # problem, which is precisely when an operator needs it most.
        logger.error("Database unreachable at startup")

    if not settings.gemini_enabled:
        logger.warning("GOOGLE_API_KEY not set - the coach will reply in degraded mode")

    # Reminders are the only part of the system that acts unasked, so they are
    # also the only part that can fail with nobody watching. Neither the bot nor
    # the sweep may take the application down: a runner who came for the web
    # client should not lose it because Telegram is unreachable.
    telegram = get_telegram()
    scheduler = None

    if settings.telegram_enabled:
        coach = get_coach()
        try:
            await telegram.start(
                on_link=handle_start,
                on_message=lambda chat_id, text, name: handle_message(
                    coach, chat_id, text, name
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Telegram bot failed to start; continuing without it")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set - reminders will not be delivered")

    if settings.reminders_enabled and settings.telegram_enabled:
        try:
            scheduler = build_scheduler(telegram)
            scheduler.start()
            logger.info("Reminder sweep every %ss", settings.reminder_sweep_seconds)
        except Exception:  # noqa: BLE001
            logger.exception("Reminder scheduler failed to start; continuing without it")
            scheduler = None

    logger.info("RunCoach started")
    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await telegram.stop()
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

# The client is served by the same process as the API, so the deployment stays a
# single service and the WebSocket shares an origin with the page.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/privacidad", include_in_schema=False)
    def privacy_es() -> FileResponse:
        """What is kept, who else sees it, and for how long.

        A separate page rather than a modal: nobody reads a modal, and the point
        is that this can be linked to and read before saying anything.
        """
        return FileResponse(WEB_DIR / "privacidad.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy_en() -> FileResponse:
        """The same notice in English.

        The coach answers in whatever language it is spoken to, so a reader who
        arrived in English should not have to work through a Spanish page to
        find out what is being stored about them.
        """
        return FileResponse(WEB_DIR / "privacy.html")
