"""Gemini client for the text path.

The Live API voice path in F2 talks to a different endpoint on the same SDK and
will live beside this, not inside it: streaming audio and request/response text
have little in common beyond the persona, which both take from
src.coaching.prompts.
"""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Roles as the API names them. The rest of the codebase says "assistant"
# because that is what the database and the HTTP contract use.
_ROLE_MAP = {"user": "user", "assistant": "model"}


class GeminiUnavailableError(RuntimeError):
    """Raised when the model cannot be reached or is not configured."""


class GeminiService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: genai.Client | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.gemini_enabled

    @property
    def model(self) -> str:
        return self._settings.gemini_model

    def _get_client(self) -> genai.Client:
        """Built on first use, never at import time.

        Constructing this eagerly would make the package unimportable without a
        key, which would take the test suite and /health down with it.
        """
        if not self.enabled:
            raise GeminiUnavailableError("GOOGLE_API_KEY is not configured")
        if self._client is None:
            self._client = genai.Client(api_key=self._settings.google_api_key)
        return self._client

    @staticmethod
    def _to_contents(
        message: str, history: list[dict[str, str]] | None
    ) -> list[types.Content]:
        contents: list[types.Content] = []
        for turn in history or []:
            role = _ROLE_MAP.get(turn.get("role", ""))
            text = turn.get("content")
            if not role or not text:
                continue
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
        contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
        return contents

    async def generate(
        self,
        message: str,
        system_prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Ask the coach a question and return the reply text.

        The persona travels as system_instruction rather than as a leading user
        turn, so a long conversation cannot push it out of the context window.
        """
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self._settings.gemini_temperature,
            max_output_tokens=self._settings.gemini_max_output_tokens,
        )

        try:
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=self._to_contents(message, history),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as one type
            logger.exception("Gemini request failed")
            raise GeminiUnavailableError(str(exc)) from exc

        text = (response.text or "").strip()

        if not text:
            # Reasoning models spend max_output_tokens on thinking, so a budget
            # that is too small returns an empty or truncated body rather than
            # an error. Name it explicitly; it is not obvious from the symptom.
            finish = response.candidates[0].finish_reason if response.candidates else None
            logger.error("Gemini returned no text (finish_reason=%s)", finish)
            raise GeminiUnavailableError(f"empty response from model (finish={finish})")

        return text
