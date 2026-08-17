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


# --- the spoken profile, through the socket ----------------------------------
#
# The agent-level tests cover reading a profile out of speech. Nothing covered
# the path through this module, which is where the F5 memory defect actually
# lived: transcripts were persisted and nothing was ever read back from them.

def test_a_completed_spoken_turn_is_persisted_and_read(voice_client, gemini, coach):
    from src import dependencies
    from src.services import db_service

    db_service.create_schema()
    gemini.extraction = {"goal": "21K", "weekly_km": 18.0}
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="corro 18 kilómetros por semana, quiero un 21K"),
        server_message(coach_text="Dieciocho es buena base."),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    sesion = "b" * 32
    with client.websocket_connect(f"/ws/voice?session_id={sesion}") as ws:
        ws.receive_json()  # ready
        tipos = [ws.receive_json()["type"] for _ in range(4)]

    # The turn ends, and the panel is told the profile moved.
    assert "turn_complete" in tipos
    assert "profile_updated" in tipos

    # Both sides of the exchange are in the same history as the text chat.
    user = db_service.get_or_create_user(web_session_id=sesion)
    history = db_service.get_history(db_service.get_or_create_conversation(user["id"]))
    assert [m["channel"] for m in history] == ["voice", "voice"]
    assert user["goal"] == "21K"
    assert user["weekly_km"] == 18.0


def test_only_what_the_runner_said_is_read_for_a_profile(voice_client, gemini, coach):
    """The coach's own words are not facts about the runner."""
    from src import dependencies
    from src.services import db_service

    db_service.create_schema()
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="hola"),
        server_message(coach_text="Corres 40 kilómetros por semana según mis notas."),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice?session_id=" + "c" * 32) as ws:
        ws.receive_json()
        for _ in range(3):
            ws.receive_json()

    enviado = [c["message"] for c in gemini.extractions]
    assert not any("40" in m for m in enviado), f"leyo lo que dijo el coach: {enviado}"


def test_a_turn_with_no_session_id_is_not_persisted(voice_client, coach):
    """An anonymous socket still works; it just leaves nothing behind."""
    from src import dependencies
    from src.services import db_service

    db_service.create_schema()
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="algo"),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
        ws.receive_json()
        assert ws.receive_json()["type"] == "turn_complete"


# --- when things fail --------------------------------------------------------

def test_a_model_that_will_not_connect_falls_back_to_text(voice_client):
    """The runner gets the text chat rather than a socket that dies silently."""
    from src.services.live_service import LiveUnavailableError

    client, live = voice_client()

    def explode(system_prompt):
        raise LiveUnavailableError("no reachable")

    live.connect = explode

    with client.websocket_connect("/ws/voice") as ws:
        message = ws.receive_json()

    assert message == {"type": "fallback", "reason": "unavailable"}


def test_an_unexpected_failure_still_ends_in_a_fallback(voice_client):
    """Whatever breaks, the browser is told to switch rather than left waiting."""
    client, live = voice_client()

    def explode(system_prompt):
        raise RuntimeError("algo raro")

    live.connect = explode

    with client.websocket_connect("/ws/voice") as ws:
        message = ws.receive_json()

    assert message == {"type": "fallback", "reason": "error"}


# --- the spoken week, through the socket --------------------------------------
#
# Voice is the headline of this product. A week given by speaking has to reach
# the panel exactly as one given in writing does, or the feature fills in for
# people who type and stays empty for the people using what it was built for.

SEMANA_HABLADA = (
    "Haz el martes cinco kilómetros suaves, el jueves otros cinco, el sábado tres "
    "muy tranquilos y el domingo la tirada larga de nueve kilómetros."
)


def test_a_week_spoken_aloud_fills_the_panel(voice_client, gemini, coach):
    from src import dependencies
    from src.services import db_service

    db_service.create_schema()
    gemini.plan_extraction = {"sessions": [
        {"day": 2, "km": 5.0, "note": "suaves"},
        {"day": 4, "km": 5.0, "note": None},
        {"day": 6, "km": 3.0, "note": "muy tranquilos"},
        {"day": 7, "km": 9.0, "note": "tirada larga"},
    ]}
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="dame el plan de la semana"),
        server_message(coach_text=SEMANA_HABLADA),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    sesion = "w" * 32
    with client.websocket_connect(f"/ws/voice?session_id={sesion}") as ws:
        ws.receive_json()  # ready
        tipos = [ws.receive_json()["type"] for _ in range(4)]

    assert "profile_updated" in tipos, "el panel nunca se enteró de la semana"

    user = db_service.get_or_create_user(web_session_id=sesion)
    assert [s["day"] for s in db_service.get_plan(user["id"])] == [2, 4, 6, 7]


def test_the_week_is_read_from_the_coach_not_from_the_runner(voice_client, gemini, coach):
    """Feeding the coach's reply to the profile extractor would store the plan's
    own numbers as the runner's current volume - the precise mistake the persona
    spends a paragraph warning itself against."""
    from src import dependencies
    from src.coaching.plan import PLAN_SCHEMA
    from src.services import db_service

    db_service.create_schema()
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="¿y el plan?"),
        server_message(coach_text=SEMANA_HABLADA),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice?session_id=" + "x" * 32) as ws:
        ws.receive_json()  # ready
        for _ in range(3):  # two transcripts and turn_complete
            ws.receive_json()

    leido_como_perfil = [
        call["message"] for call in gemini.extractions if call["schema"] is not PLAN_SCHEMA
    ]
    assert SEMANA_HABLADA not in leido_como_perfil


def test_a_spoken_turn_with_no_week_spends_nothing_on_reading_one(voice_client, gemini, coach):
    from src import dependencies
    from src.services import db_service
    from src.coaching.plan import PLAN_SCHEMA

    db_service.create_schema()
    app.dependency_overrides[dependencies.get_coach] = lambda: coach

    session = FakeSession([
        server_message(user_text="gracias"),
        server_message(coach_text="A ti. Nos vemos mañana."),
        server_message(turn_complete=True),
    ])
    client, _ = voice_client(session)

    with client.websocket_connect("/ws/voice?session_id=" + "y" * 32) as ws:
        ws.receive_json()
        for _ in range(3):
            ws.receive_json()

    assert not any(call["schema"] is PLAN_SCHEMA for call in gemini.extractions)
