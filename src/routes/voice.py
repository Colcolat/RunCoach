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

from src.coaching.prompts import build_system_prompt
from src.config import Settings, get_settings
from src.dependencies import get_live
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


async def _pump_model(websocket: WebSocket, session) -> None:
    """Model to browser: audio to play, transcripts to display and to persist."""
    async for reply in session.receive():
        content = reply.server_content
        if not content:
            continue

        if content.input_transcription and content.input_transcription.text:
            await websocket.send_json(
                {
                    "type": "transcript",
                    "role": "user",
                    "text": content.input_transcription.text,
                }
            )

        if content.output_transcription and content.output_transcription.text:
            await websocket.send_json(
                {
                    "type": "transcript",
                    "role": "coach",
                    "text": content.output_transcription.text,
                }
            )

        if content.model_turn:
            for part in content.model_turn.parts:
                blob = getattr(part, "inline_data", None)
                if blob and blob.data:
                    await websocket.send_bytes(blob.data)

        if content.turn_complete:
            await websocket.send_json({"type": "turn_complete"})


@router.websocket("/ws/voice")
async def voice(
    websocket: WebSocket,
    live: LiveVoiceService = Depends(get_live),
    settings: Settings = Depends(get_settings),
) -> None:
    await websocket.accept()

    if not live.enabled:
        await websocket.send_json({"type": "fallback", "reason": "not_configured"})
        await websocket.close()
        return

    budget = live.new_budget()

    try:
        async with live.connect(build_system_prompt({})) as session:
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
            model = asyncio.create_task(_pump_model(websocket, session))

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
