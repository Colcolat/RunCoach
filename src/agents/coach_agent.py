"""Coach orchestration.

The agent never delivers its own reply. It composes the prompt, asks the model
and returns text; whether that becomes an HTTP response, spoken audio or a
Telegram message is the caller's decision. That is what lets one coach serve the
web client and the bot without duplicating the pipeline.

Since F3 it also remembers: `converse` resolves the runner, replays their
history, and persists both sides of the exchange.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.coaching.prompts import build_system_prompt, welcome_message
from src.config import get_settings
from src.services import db_service
from src.services.gemini_service import GeminiService, GeminiUnavailableError

logger = logging.getLogger(__name__)

DEGRADED_MESSAGE = (
    "Ahora mismo no puedo consultar con el entrenador. Vuelve a intentarlo en un "
    "momento y seguimos con tu plan."
)


@dataclass(frozen=True)
class CoachReply:
    """A reply, plus whether it came from the model or from a fallback."""

    text: str
    degraded: bool = False
    conversation_id: int | None = None


class CoachAgent:
    def __init__(self, gemini: GeminiService, db=db_service) -> None:
        self._gemini = gemini
        self._db = db

    async def handle_message(
        self,
        message: str,
        profile: dict | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> CoachReply:
        """Answer a single message. Stateless: caller supplies any context."""
        text = (message or "").strip()
        if not text:
            raise ValueError("message must not be empty")

        try:
            reply = await self._gemini.generate(
                message=text,
                system_prompt=build_system_prompt(profile),
                history=history,
            )
        except GeminiUnavailableError:
            # Degrade rather than propagate: a runner mid-conversation should
            # get an answer, and /health already reports the real cause.
            logger.warning("Coach falling back to degraded reply")
            return CoachReply(text=DEGRADED_MESSAGE, degraded=True)

        return CoachReply(text=reply)

    async def converse(
        self,
        message: str,
        web_session_id: str | None = None,
        telegram_id: int | None = None,
        username: str | None = None,
        channel: str = "web",
    ) -> CoachReply:
        """Answer with memory: load the runner and their history, then persist.

        The stateless path above is what the F2 voice proxy uses, because the
        Live API keeps its own session context. This one is for request and
        response interfaces, which have none.
        """
        text = (message or "").strip()
        if not text:
            raise ValueError("message must not be empty")

        user = self._db.get_or_create_user(
            web_session_id=web_session_id, telegram_id=telegram_id, username=username
        )
        conversation_id = self._db.get_or_create_conversation(user["id"])
        history = self._db.get_history(conversation_id, limit=get_settings().history_limit)

        reply = await self.handle_message(text, profile=user, history=history)

        # Persist after answering, and persist both sides. Saving the question
        # first would leave an unanswered turn in history if the model failed,
        # and the next request would replay it as if it had been answered.
        self._db.save_message(conversation_id, "user", text, channel=channel)
        self._db.save_message(conversation_id, "assistant", reply.text, channel=channel)

        return CoachReply(
            text=reply.text, degraded=reply.degraded, conversation_id=conversation_id
        )

    def remember_voice_turn(
        self, web_session_id: str, role: str, content: str
    ) -> int | None:
        """Persist a transcript from a spoken turn.

        Voice and text share one history, so a conversation started out loud
        continues in writing without losing context. Failure here must not take
        the voice session down: losing a transcript is worse than a crash only
        if it also drops the call.
        """
        text = (content or "").strip()
        if not text:
            return None
        try:
            user = self._db.get_or_create_user(web_session_id=web_session_id)
            conversation_id = self._db.get_or_create_conversation(user["id"])
            self._db.save_message(conversation_id, role, text, channel="voice")
            return conversation_id
        except Exception:  # noqa: BLE001
            logger.exception("Could not persist a voice transcript")
            return None

    def welcome(self, username: str | None = None) -> CoachReply:
        """Open a conversation without spending a request.

        The free tier caps daily requests, so the greeting is written rather
        than generated. It also means the app answers instantly on load.
        """
        return CoachReply(text=welcome_message(username))
