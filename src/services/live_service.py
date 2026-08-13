"""Gemini Live: bidirectional voice.

Separate from GeminiService because the two have almost nothing in common
beyond the persona. That one makes a request and gets a response; this one holds
an open socket and streams audio both ways.

Audio contract, measured against the API rather than assumed:

  input   PCM 16-bit signed, mono, little-endian, 16000 Hz
          declared as "audio/pcm;rate=16000"
  output  PCM 16-bit signed, mono, 24000 Hz

The rates differ, which is not a mistake: the API sends back a higher rate than
it accepts, and the browser has to be told both.
"""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
INPUT_MIME = f"audio/pcm;rate={INPUT_SAMPLE_RATE}"

BYTES_PER_SAMPLE = 2  # 16-bit mono


class LiveUnavailableError(RuntimeError):
    """Raised when the voice session cannot be opened."""


def audio_seconds(byte_count: int, sample_rate: int = INPUT_SAMPLE_RATE) -> float:
    """Duration of a raw PCM buffer.

    Used for the session budget, so it is worth stating the arithmetic: one
    sample is two bytes, and there is one channel.
    """
    return byte_count / (sample_rate * BYTES_PER_SAMPLE)


class VoiceBudget:
    """Caps how much voice a single session may spend.

    The free tier limits tokens per minute, and audio consumes far more of them
    than text. Without a cap, one long conversation could exhaust the quota and
    leave the next visitor with a mute demo. When the budget runs out the
    session falls back to text rather than failing.
    """

    def __init__(self, max_seconds: float) -> None:
        self.max_seconds = max_seconds
        self.spent_seconds = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_seconds - self.spent_seconds)

    @property
    def exhausted(self) -> bool:
        return self.spent_seconds >= self.max_seconds

    def charge(self, byte_count: int) -> None:
        self.spent_seconds += audio_seconds(byte_count)


class LiveVoiceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: genai.Client | None = None

    @property
    def enabled(self) -> bool:
        return self._settings.gemini_enabled

    @property
    def model(self) -> str:
        return self._settings.gemini_live_model

    def new_budget(self) -> VoiceBudget:
        return VoiceBudget(self._settings.voice_max_seconds)

    def build_config(self, system_prompt: str) -> types.LiveConnectConfig:
        """Assemble the session configuration.

        Transcription of both directions is switched on deliberately. It costs
        nothing in audio quality, both were measured arriving in the same
        stream, and without it a spoken conversation would leave no text to
        persist, which is what F3 needs to remember it.
        """
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_prompt,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    def _get_client(self) -> genai.Client:
        if not self.enabled:
            raise LiveUnavailableError("GOOGLE_API_KEY is not configured")
        if self._client is None:
            self._client = genai.Client(api_key=self._settings.google_api_key)
        return self._client

    def connect(self, system_prompt: str):
        """Open a Live session.

        Returns the SDK's async context manager unchanged, so the caller keeps
        `async with` semantics and the socket closes on the way out even if the
        connection drops mid-conversation.
        """
        return self._get_client().aio.live.connect(
            model=self.model, config=self.build_config(system_prompt)
        )
