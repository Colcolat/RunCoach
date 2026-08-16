"""WebSocket proxy between the browser and Gemini Live.

The browser never holds the API key, so every frame passes through here. Two
tasks run concurrently for the life of a session: one pumps microphone audio
towards the model, the other pumps the model's audio and transcripts back.

Wire protocol, browser to server:
  binary frame   raw PCM 16-bit mono 16 kHz
  {"type": "end"}      the runner stopped talking
  {"type": "close"}    hang up

Server to browser:
  binary frame            raw PCM 16-bit mono 24 kHz, play it
  {"type": "ready", ...}  session open, carries the audio rates
  {"type": "transcript", "role": "user"|"coach", "text": ...}
  {"type": "budget", "remaining": seconds}
  {"type": "fallback", "reason": ...}  switch to the text chat
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from google.genai import types

from src.agents.coach_agent import CoachAgent
from src.coaching.prompts import build_system_prompt
from src.config import Settings, get_settings
from src.dependencies import get_coach, get_live
from src.services.live_service import (
    INPUT_MIME,
    INPUT_SAMPLE_RATE,
    OUTPUT_SAMPLE_RATE,
    LiveUnavailableError,
    LiveVoiceService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


async def _pump_microphone(
    websocket: WebSocket, session, budget, settings: Settings
) -> str:
    """Browser to model. Returns why it stopped."""
    while True:
        try:
            message = await asyncio.wait_for(
                websocket.receive(), timeout=settings.voice_idle_timeout
            )
        except asyncio.TimeoutError:
            return "idle"

        if message["type"] == "websocket.disconnect":
            return "disconnected"

        audio = message.get("bytes")
        if audio:
            budget.charge(len(audio))
            if budget.exhausted:
                # Stop before sending, so the cap is a real ceiling rather than
                # something noticed one chunk too late.
                return "budget"
            await session.send_realtime_input(
                audio=types.Blob(data=audio, mime_type=INPUT_MIME)
            )
            continue

        text = message.get("text")
        if text:
            if '"end"' in text:
                await session.send_realtime_input(audio_stream_end=True)
            elif '"close"' in text:
                return "closed"


async def _pump_model(
    websocket: WebSocket, session, coach: CoachAgent, session_id: str | None
) -> None:
    """Model to browser: audio to play, transcripts to display and to persist.

    Transcripts arrive in fragments, so they are accumulated per speaker and
    written once the turn completes. Saving each fragment would shred one
    sentence across a dozen rows and make the replayed history unreadable.
    """
    spoken = {"user": [], "coach": []}

    async for reply in session.receive():
        content = reply.server_content
        if not content:
            continue

        if content.input_transcription and content.input_transcription.text:
            fragment = content.input_transcription.text
            spoken["user"].append(fragment)
            await websocket.send_json(
                {"type": "transcript", "role": "user", "text": fragment}
            )

        if content.output_transcription and content.output_transcription.text:
            fragment = content.output_transcription.text
            spoken["coach"].append(fragment)
            await websocket.send_json(
                {"type": "transcript", "role": "coach", "text": fragment}
            )

        if content.model_turn:
            for part in content.model_turn.parts:
                blob = getattr(part, "inline_data", None)
                if blob and blob.data:
                    await websocket.send_bytes(blob.data)

        if content.turn_complete:
            if session_id:
                said = _persist_turn(coach, session_id, spoken)
                # Reading the profile is a side effect of the turn, so it runs
                # detached: awaiting it here would hold up the audio pump, and
                # this is a live conversation where that is audible.
                if said:
                    asyncio.create_task(
                        _read_profile_from_speech(websocket, coach, session_id, said)
                    )
            spoken = {"user": [], "coach": []}
            await websocket.send_json({"type": "turn_complete"})


def _persist_turn(coach: CoachAgent, session_id: str, spoken: dict) -> str:
    """Write a completed spoken exchange into the same history as the text chat.

    Returns what the runner said, which is the half worth reading a profile out
    of: the coach's own words are not facts about the runner.
    """
    said = ""
    for role, key in (("user", "user"), ("assistant", "coach")):
        text = "".join(spoken[key]).strip()
        if text:
            coach.remember_voice_turn(session_id, role, text)
            if role == "user":
                said = text
    return said


async def _read_profile_from_speech(
    websocket: WebSocket, coach: CoachAgent, session_id: str, said: str
) -> None:
    """Learn from a spoken turn, and tell the browser if anything changed.

    The panel is what makes this visible: a runner who says their goal out loud
    should watch it appear, exactly as it does when they type it.
    """
    try:
        if await coach.read_spoken_profile(session_id, said):
            await websocket.send_json({"type": "profile_updated"})
    except Exception:  # noqa: BLE001 - a side effect must never end the call
        logger.exception("Reading a profile from speech failed")


@router.websocket("/ws/voice")
async def voice(
    websocket: WebSocket,
    session_id: str | None = None,
    live: LiveVoiceService = Depends(get_live),
    coach: CoachAgent = Depends(get_coach),
    settings: Settings = Depends(get_settings),
) -> None:
    # WebSockets are exempt from the same-origin policy, so the browser will
    # happily let any page open one against us. Checked before accepting, so a
    # rejected connection never reaches the Live API and never costs a token.
    allowed = settings.origins
    if allowed:
        origin = websocket.headers.get("origin")
        if origin not in allowed:
            logger.warning("Voice socket refused for origin %r", origin)
            await websocket.close(code=1008)
            return

    await websocket.accept()

    if not live.enabled:
        await websocket.send_json({"type": "fallback", "reason": "not_configured"})
        await websocket.close()
        return

    budget = live.new_budget()

    # The spoken coach gets the same briefing as the written one. Opening the
    # session with an empty profile was a real defect found in use: a runner who
    # said their goal and volume out loud was asked for them again a turn later,
    # because nothing the voice path learned ever reached the prompt, and
    # nothing the text path had learned reached it either.
    profile = coach.profile_for(session_id)

    try:
        async with live.connect(build_system_prompt(profile)) as session:
            await websocket.send_json(
                {
                    "type": "ready",
                    "input_sample_rate": INPUT_SAMPLE_RATE,
                    "output_sample_rate": OUTPUT_SAMPLE_RATE,
                    "budget_seconds": budget.max_seconds,
                }
            )

            mic = asyncio.create_task(
                _pump_microphone(websocket, session, budget, settings)
            )
            model = asyncio.create_task(_pump_model(websocket, session, coach, session_id))

            # Whichever finishes first ends the session: the runner hung up, or
            # the model stream closed. The other is cancelled rather than left
            # awaiting a socket nobody is reading.
            done, pending = await asyncio.wait(
                {mic, model}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            reason = next((t.result() for t in done if t is mic), None)
            if reason == "budget":
                await websocket.send_json(
                    {"type": "fallback", "reason": "budget_exhausted"}
                )

    except LiveUnavailableError:
        logger.warning("Voice session refused: model unavailable")
        await _safe_json(websocket, {"type": "fallback", "reason": "unavailable"})
    except WebSocketDisconnect:
        logger.info("Voice session closed by the browser")
    except Exception:  # noqa: BLE001 - never leave the socket hanging
        logger.exception("Voice session failed")
        await _safe_json(websocket, {"type": "fallback", "reason": "error"})
    finally:
        await _safe_close(websocket)


async def _safe_json(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:  # noqa: BLE001 - the browser may already be gone
        pass


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:  # noqa: BLE001
        pass
