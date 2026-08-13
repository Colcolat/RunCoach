"""Coach orchestration.

The agent never delivers its own reply. It composes the prompt, asks the model
and returns text; whether that becomes an HTTP response, spoken audio or a
Telegram message is the caller's decision. That is what lets one coach serve the
web client and the bot without duplicating the pipeline.

`profile` and `history` are already parameters even though F1 always passes
nothing: F3 fills them from the database without changing this signature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.coaching.prompts import build_system_prompt, welcome_message
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


class CoachAgent:
    def __init__(self, gemini: GeminiService) -> None:
        self._gemini = gemini

    async def handle_message(
        self,
        message: str,
        profile: dict | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> CoachReply:
        text = (message or "").strip()
        if not text:
            raise ValueError("message must not be empty")

        system_prompt = build_system_prompt(profile)

        try:
            reply = await self._gemini.generate(
                message=text, system_prompt=system_prompt, history=history
            )
        except GeminiUnavailableError:
            # Degrade rather than propagate: a runner mid-conversation should
            # get an answer, and /health already reports the real cause.
            logger.warning("Coach falling back to degraded reply")
            return CoachReply(text=DEGRADED_MESSAGE, degraded=True)

        return CoachReply(text=reply)

    def welcome(self, username: str | None = None) -> CoachReply:
        """Open a conversation without spending a request.

        The free tier caps daily requests, so the greeting is written rather
        than generated. It also means the app answers instantly on load.
        """
        return CoachReply(text=welcome_message(username))
