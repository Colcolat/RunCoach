"""Gemini client for the text path.

The Live API voice path in F2 talks to a different endpoint on the same SDK and
will live beside this, not inside it: streaming audio and request/response text
have little in common beyond the persona, which both take from
src.coaching.prompts.
"""

from __future__ import annotations

import json
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


class GeminiRateLimitedError(GeminiUnavailableError):
    """Raised when the quota rejects the request.

    A subclass rather than a separate type, so every existing caller keeps
    degrading exactly as before while the ones that care can tell a limit that
    clears in a minute apart from a model that is genuinely down. Telling a
    runner "vuelve más tarde" when the answer is "espera veinte segundos" is a
    small lie that makes the app feel broken.
    """


def _is_rate_limit(exc: Exception) -> bool:
    """Whether an SDK error is a 429.

    The SDK's ClientError takes the status code as its first argument but does
    not expose it as an attribute, so this reads it defensively and falls back
    to the message. Getting this wrong is safe in one direction only: a missed
    429 degrades as before, while a false positive would tell a runner to wait
    for a limit that is not the problem.
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429:
        return True
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text


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

    @property
    def extraction_model(self) -> str:
        """A different id from `model`, so the two draw on separate rate limits."""
        return self._settings.gemini_extraction_model

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
            if _is_rate_limit(exc):
                logger.warning("Gemini refused the request: rate limited")
                raise GeminiRateLimitedError(str(exc)) from exc
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

    async def extract(
        self,
        message: str,
        system_prompt: str,
        schema: dict,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        """Ask for a JSON object matching `schema` and return it parsed.

        The schema comes from the caller rather than living here, so the domain
        keeps ownership of its own vocabulary and this client stays generic.

        Temperature is zero: this is a reading task, and the variation that
        makes coaching prose sound human makes extraction unreliable.
        """
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.0,
            max_output_tokens=self._settings.gemini_extraction_max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
        )

        try:
            response = await client.aio.models.generate_content(
                model=self.extraction_model,
                contents=self._to_contents(message, history),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller as one type
            if _is_rate_limit(exc):
                logger.warning("Gemini refused the extraction: rate limited")
                raise GeminiRateLimitedError(str(exc)) from exc
            logger.exception("Gemini extraction request failed")
            raise GeminiUnavailableError(str(exc)) from exc

        try:
            parsed = json.loads(response.text or "")
        except (TypeError, ValueError) as exc:
            # JSON mode makes this unlikely rather than impossible: a response
            # truncated by the token budget is still valid JSON mode output and
            # still unparseable.
            logger.error("Gemini returned unparseable JSON: %r", response.text)
            raise GeminiUnavailableError("model returned unparseable JSON") from exc

        if not isinstance(parsed, dict):
            raise GeminiUnavailableError(f"expected a JSON object, got {type(parsed).__name__}")

        return parsed
