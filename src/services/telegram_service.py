"""Telegram delivery.

Long polling rather than a webhook, and that is a deliberate choice rather than
the easy one. A webhook needs a public HTTPS address, which does not exist until
F7 and never exists on a laptop, and it needs a secret so that anyone who finds
the URL cannot post updates as Telegram. The first version of this project
shipped a webhook that accepted unauthenticated requests, which is exactly the
failure this avoids by not having the endpoint at all.

Polling costs an outbound connection and works identically in every environment.

This module delivers and receives. It decides nothing: what a reminder says
lives in src.coaching.reminders, and who gets one lives in the sweep.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable
from urllib.parse import quote

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

# What the bot says when someone opens it without coming from the web. Written
# rather than generated: it is the first thing anyone sees and it must not
# depend on the model being reachable.
UNLINKED_GREETING = (
    "Hola, soy RunCoach. Para conectar esta conversación con tu entrenador, abre "
    "la aplicación web y pulsa \"Conectar Telegram\". Así no pierdes lo que ya "
    "habías hablado."
)

LINKED_GREETING = (
    "Listo, ya estamos conectados. Te avisaré por aquí cuando toque entrenar, y "
    "puedes escribirme cuando quieras."
)


class TelegramUnavailableError(RuntimeError):
    """Raised when the bot is not configured or Telegram cannot be reached."""


class TelegramService:
    """Owns the bot application and its lifecycle.

    Nothing is constructed at import time, for the same reason as the Gemini
    client: the package has to stay importable without a token so the suite and
    the health check keep working.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._app: Application | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.telegram_enabled

    @property
    def username(self) -> str:
        return self._settings.telegram_bot_username.lstrip("@")

    def deep_link(self, session_id: str) -> str | None:
        """The t.me address that links a browser session to a Telegram chat.

        Telegram hands whatever follows `start=` to the /start handler, so the
        session id travels without a code table, an expiry or a second step.
        Returns None when no username is configured, so the client hides the
        button rather than offering a broken link.

        The id is percent-encoded rather than trusted. It reaches here straight
        off a URL path, and a space or an ampersand in it would produce an
        address that either breaks or carries something we did not intend.
        """
        if not self.username or not session_id:
            return None
        return f"https://t.me/{self.username}?start={quote(session_id, safe='')}"

    async def start(
        self,
        on_link: Callable[[str, int, str | None], Awaitable[str]],
        on_message: Callable[[int, str, str | None], Awaitable[str]],
    ) -> None:
        """Bring the bot up and begin polling.

        The two callbacks are supplied by the composition root, so this module
        never imports the agent or the database and stays a transport.
        """
        if not self.enabled:
            logger.info("Telegram not configured; the bot will not start")
            return

        app = Application.builder().token(self._settings.telegram_bot_token).build()

        async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat_id = update.effective_chat.id
            name = update.effective_user.first_name if update.effective_user else None
            # context.args holds whatever followed `start=` in the deep link.
            payload = context.args[0] if context.args else ""
            reply = await on_link(payload, chat_id, name)
            await update.message.reply_text(reply)

        async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            # effective_message, not message: an edited message arrives as
            # edited_message and still reaches this handler, where update.message
            # is None and every attribute on it raises.
            incoming = update.effective_message
            if incoming is None:
                return

            chat_id = update.effective_chat.id
            name = update.effective_user.first_name if update.effective_user else None
            reply = await on_message(chat_id, incoming.text or "", name)
            await incoming.reply_text(reply)

        app.add_handler(CommandHandler("start", start_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        self._app = app
        logger.info("Telegram bot polling")

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception:  # noqa: BLE001 - shutdown must not mask the real cause
            logger.exception("Telegram bot did not shut down cleanly")
        finally:
            self._app = None
            logger.info("Telegram bot stopped")

    async def send(self, chat_id: int, text: str) -> None:
        """Deliver one message. Raises so the sweep can decide what to do."""
        if self._app is None:
            raise TelegramUnavailableError("the bot is not running")
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:  # noqa: BLE001 - one type for the caller
            raise TelegramUnavailableError(str(exc)) from exc
