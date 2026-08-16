"""What the bot does when someone writes to it.

Kept apart from the transport so that src.services.telegram_service never
imports the agent or the database, and apart from the agent so the agent still
does not know what a chat id is. This module is the seam between them.
"""

from __future__ import annotations

import logging

from src.agents.coach_agent import CoachAgent
from src.services import db_service
from src.services.telegram_service import LINKED_GREETING, UNLINKED_GREETING

logger = logging.getLogger(__name__)

# A session id is 32 hex characters, minted by crypto.randomUUID in the browser.
# Anything else arriving as a deep-link payload is not one of ours.
SESSION_ID_LENGTH = 32

TROUBLE = (
    "Ahora mismo no puedo responderte. Inténtalo en un momento y seguimos con "
    "tu plan."
)


def _looks_like_a_session(payload: str) -> bool:
    candidate = (payload or "").strip()
    if len(candidate) != SESSION_ID_LENGTH:
        return False
    return all(c in "0123456789abcdef" for c in candidate.lower())


async def handle_start(payload: str, chat_id: int, name: str | None) -> str:
    """Attach this chat to the web conversation named in the deep link.

    Someone who opens the bot directly, with no payload, gets told how to link
    rather than getting a fresh empty profile. Two rows for one runner is
    exactly what the linking exists to prevent, and silently creating the second
    one here would defeat it.
    """
    if not _looks_like_a_session(payload):
        return UNLINKED_GREETING

    try:
        user = db_service.link_telegram(payload.strip(), chat_id, username=name)
        db_service.ensure_inactivity_reminder(user["id"])
    except Exception:  # noqa: BLE001 - the runner gets an answer either way
        logger.exception("Could not link Telegram chat %s", chat_id)
        return TROUBLE

    logger.info("Linked Telegram chat %s to user %s", chat_id, user["id"])
    return LINKED_GREETING


async def handle_message(coach: CoachAgent, chat_id: int, text: str, name: str | None) -> str:
    """Coach a turn that arrived over Telegram.

    The same agent, the same history and the same profile as the web: a runner
    who asks by phone what they asked in the browser is the same runner, and
    gets an answer that knows it.
    """
    message = (text or "").strip()
    if not message:
        return "Cuéntame algo y seguimos."

    try:
        reply = await coach.converse(
            message, telegram_id=chat_id, username=name, channel="telegram"
        )
    except Exception:  # noqa: BLE001 - never leave a message unanswered
        logger.exception("Telegram turn failed for chat %s", chat_id)
        return TROUBLE

    return reply.text
