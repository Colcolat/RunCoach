"""Tests for the voice service that need no network.

The audio arithmetic is worth pinning down: a wrong bytes-per-second constant
would make the session budget silently wrong, and the symptom would be a demo
that goes mute early for no visible reason.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.live_service import (
    INPUT_MIME,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    LiveUnavailableError,
    LiveVoiceService,
    VoiceBudget,
    audio_seconds,
)


# --- the audio contract, measured against the API ----------------------------


def test_the_rates_differ_in_each_direction():
    """Not a typo: the API accepts 16 kHz and sends back 24 kHz."""
    assert INPUT_SAMPLE_RATE == 16000
    assert OUTPUT_SAMPLE_RATE == 24000


def test_the_input_mime_declares_the_rate_the_api_expects():
    assert INPUT_MIME == "audio/pcm;rate=16000"


def test_one_second_of_input_audio_is_32000_bytes():
    """16000 samples per second, 2 bytes each, one channel."""
    assert audio_seconds(32000) == pytest.approx(1.0)


def test_audio_seconds_accepts_another_rate_for_the_output_stream():
    assert audio_seconds(48000, sample_rate=OUTPUT_SAMPLE_RATE) == pytest.approx(1.0)


# --- the session budget ------------------------------------------------------


def test_a_fresh_budget_has_everything_remaining():
    budget = VoiceBudget(max_seconds=60)

    assert budget.remaining == 60
    assert budget.exhausted is False


def test_charging_consumes_the_measured_duration():
    budget = VoiceBudget(max_seconds=10)

    budget.charge(32000)  # one second

    assert budget.spent_seconds == pytest.approx(1.0)
    assert budget.remaining == pytest.approx(9.0)


def test_the_budget_reports_exhaustion_once_spent():
    budget = VoiceBudget(max_seconds=1)

    budget.charge(32000)

    assert budget.exhausted is True


def test_remaining_never_goes_negative():
    """The UI shows this number; a negative countdown would be nonsense."""
    budget = VoiceBudget(max_seconds=1)

    budget.charge(32000 * 5)

    assert budget.remaining == 0.0
    assert budget.exhausted is True


# --- service configuration ---------------------------------------------------


def _service(**overrides) -> LiveVoiceService:
    return LiveVoiceService(settings=Settings(**overrides))


def test_the_service_is_disabled_without_a_key():
    assert _service(google_api_key="").enabled is False


def test_connecting_without_a_key_raises_rather_than_hanging():
    with pytest.raises(LiveUnavailableError):
        _service(google_api_key="").connect("eres un coach")


def test_the_live_model_comes_from_configuration():
    """The Live model is a preview; a retirement must be an env change."""
    service = _service(google_api_key="k", gemini_live_model="otro-live")

    assert service.model == "otro-live"


def test_the_session_config_requests_audio_only():
    config = _service(google_api_key="k").build_config("eres un coach")

    assert config.response_modalities == ["AUDIO"]


def test_the_persona_reaches_the_voice_session():
    config = _service(google_api_key="k").build_config("REGLA DEL DIEZ POR CIENTO")

    assert "REGLA DEL DIEZ POR CIENTO" in config.system_instruction


def test_both_transcriptions_are_enabled():
    """Without these a spoken conversation would leave nothing for F3 to store."""
    config = _service(google_api_key="k").build_config("eres un coach")

    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None


def test_the_budget_length_comes_from_configuration():
    assert _service(google_api_key="k", voice_max_seconds=42).new_budget().max_seconds == 42
