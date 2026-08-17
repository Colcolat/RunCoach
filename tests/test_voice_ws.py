"""WebSocket proxy tests, driven by a fake Live session.

These exercise the wire protocol the browser depends on: that audio reaches the
model, that transcripts and audio come back, and that the budget ends the
session with a fallback instead of a broken socket.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src import dependencies
from src.main import app
from src.services.live_service import LiveVoiceService, VoiceBudget


class FakeSession:
    """Stands in for a Gemini Live session.

    Records what was sent, and replays a scripted sequence of server messages.
    """

    def __init__(self, script=None) -> None:
        self.sent_audio: list[bytes] = []
        self.stream_ended = False
        self._script = script or []

    async def send_realtime_input(self, audio=None, audio_stream_end=None):
        if audio is not None:
            self.sent_audio.append(audio.data)
        if audio_stream_end:
            self.stream_ended = True

    async def receive(self):
        for message in self._script:
            yield message
        # Then stay open, like a real session waiting for the runner to speak.
        await asyncio.sleep(3600)


def server_message(*, user_text=None, coach_text=None, audio=None, turn_complete=False):
    parts = [SimpleNamespace(inline_data=SimpleNamespace(data=audio))] if audio else []
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text=user_text) if user_text else None,
            output_transcription=SimpleNamespace(text=coach_text) if coach_text else None,
            model_turn=SimpleNamespace(parts=parts) if parts else None,
            turn_complete=turn_complete,
        )
    )


class FakeLive:
    def __init__(self, session: FakeSession, enabled: bool = True, max_seconds: float = 300):
        self.session = session
        self.enabled = enabled
        self.model = "fake-live"
        self._max_seconds = max_seconds
        self.system_prompt: str | None = None

    def new_budget(self) -> VoiceBudget:
        return VoiceBudget(self._max_seconds)

    def connect(self, system_prompt: str):
        self.system_prompt = system_prompt
        session = self.session

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture
def voice_client():
    def _build(session=None, **kwargs):
        live = FakeLive(session or FakeSession(), **kwargs)
        app.dependency_overrides[dependencies.get_live] = lambda: live
        from fastapi.testclient import TestClient

        return TestClient(app), live

    yield _build
    app.dependency_overrides.clear()


# --- handshake ---------------------------------------------------------------


def test_the_handshake_tells_the_browser_both_sample_rates(voice_client):
    """The browser cannot capture or play correctly without these two numbers."""
    client, _ = voice_client()

    with client.websocket_connect("/ws/voice") as ws:
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert ready["input_sample_rate"] == 16000
    assert ready["output_sample_rate"] == 24000
    assert ready["budget_seconds"] == 300


def test_the_coaching_persona_reaches_the_voice_session(voice_client):
    client, live = voice_client()

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()

    assert "diez por ciento" in live.system_prompt


def test_an_unconfigured_model_falls_back_instead_of_failing(voice_client):
    client, _ = voice_client(enabled=False)

    with client.websocket_connect("/ws/voice") as ws:
        message = ws.receive_json()

    assert message["type"] == "fallback"
    assert message["reason"] == "not_configured"


# --- browser to model --------------------------------------------------------


def test_microphone_audio_reaches_the_model(voice_client):
    session = FakeSession()
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x01\x02" * 800)
        ws.send_json({"type": "close"})

    assert session.sent_audio == [b"\x01\x02" * 800]


def test_the_end_signal_closes_the_input_stream(voice_client):
    session = FakeSession()
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_json({"type": "end"})
        ws.send_json({"type": "close"})

    assert session.stream_ended is True


# --- model to browser --------------------------------------------------------


def test_transcripts_of_both_speakers_reach_the_browser(voice_client):
    session = FakeSession([
        server_message(user_text="corro veinte kilómetros"),
        server_message(coach_text="buen punto de partida"),
    ])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        first = ws.receive_json()
        second = ws.receive_json()

    assert (first["role"], first["text"]) == ("user", "corro veinte kilómetros")
    assert (second["role"], second["text"]) == ("coach", "buen punto de partida")


def test_model_audio_is_forwarded_as_binary(voice_client):
    session = FakeSession([server_message(audio=b"PCMDATA" * 10)])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        assert ws.receive_bytes() == b"PCMDATA" * 10


def test_turn_completion_is_announced(voice_client):
    session = FakeSession([server_message(coach_text="listo", turn_complete=True)])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.receive_json()  # the transcript
        assert ws.receive_json()["type"] == "turn_complete"


# --- the budget --------------------------------------------------------------


def test_exhausting_the_budget_falls_back_to_text(voice_client):
    """One long conversation must not leave the next visitor with a mute demo."""
    session = FakeSession()
    client, _ = voice_client(session, max_seconds=0.5)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * 32000)  # a full second, over the half-second cap
        mensajes = [ws.receive_json(), ws.receive_json()]

    tipos = [m["type"] for m in mensajes]
    assert tipos == ["budget", "fallback"]
    assert mensajes[1]["reason"] == "budget_exhausted"


def test_the_runner_is_warned_before_the_voice_stops(voice_client):
    """This module's header promised a budget message since F2 and nothing ever
    sent one, so the microphone simply stopped answering. Being cut off without
    notice is indistinguishable from the application breaking."""
    session = FakeSession()
    client, _ = voice_client(session, max_seconds=10.0)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * 256000)  # eight seconds: past the warning, under the cap
        message = ws.receive_json()

    assert message["type"] == "budget"
    assert message["remaining"] == 2


def test_the_warning_is_said_once_and_not_on_every_frame(voice_client):
    """It is asked on every audio frame, dozens a second. A warning repeated at
    that rate is not a warning."""
    session = FakeSession()
    client, _ = voice_client(session, max_seconds=10.0)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * 256000)
        assert ws.receive_json()["type"] == "budget"
        ws.send_bytes(b"\x00" * 16000)  # still under the cap, already warned
        ws.send_bytes(b"\x00" * 320000)  # now over it
        assert ws.receive_json()["type"] == "fallback"


def test_audio_over_the_cap_is_not_forwarded_to_the_model(voice_client):
    """The cap is a ceiling, not something noticed one chunk too late."""
    session = FakeSession()
    client, _ = voice_client(session, max_seconds=0.5)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * 32000)
        ws.receive_json()

    assert session.sent_audio == []
