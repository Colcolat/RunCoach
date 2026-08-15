"""Text chat endpoints.

Every request carries a session id. The browser mints one on first visit and
keeps it in localStorage, so a returning runner keeps their history with no
login. It is not authenticated, which is fine for a demo and stated as a
limitation in the README.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.agents.coach_agent import CoachAgent
from src.coaching.prompts import weeks_until
from src.dependencies import get_coach
from src.services import db_service

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=100)


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    conversation_id: int | None = None
    degraded: bool = False


class WelcomeResponse(BaseModel):
    greeting: str
    session_id: str


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[dict[str, str]]


class ProfileResponse(BaseModel):
    """What the coach knows about a runner, for the profile panel.

    Every field is optional because nothing is ever asked for directly: the
    profile fills in from conversation, so a first visit is legitimately empty
    and the panel has to render that state.
    """

    session_id: str
    username: str | None = None
    goal: str | None = None
    experience_level: str | None = None
    weekly_km: float | None = None
    race_date: str | None = None
    # Computed here rather than in the browser, so the arithmetic the coach
    # reasons with and the number the runner reads cannot drift apart.
    weeks_to_race: int | None = None


@router.get("/welcome", response_model=WelcomeResponse)
def welcome(
    session_id: str | None = None,
    username: str | None = None,
    coach: CoachAgent = Depends(get_coach),
) -> WelcomeResponse:
    return WelcomeResponse(
        greeting=coach.welcome(username).text,
        session_id=session_id or uuid.uuid4().hex,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, coach: CoachAgent = Depends(get_coach)
) -> ChatResponse:
    session_id = payload.session_id or uuid.uuid4().hex

    try:
        reply = await coach.converse(
            payload.message,
            web_session_id=session_id,
            username=payload.username,
            channel="web",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return ChatResponse(
        reply=reply.text,
        session_id=session_id,
        conversation_id=reply.conversation_id,
        degraded=reply.degraded,
    )


@router.get("/history/{session_id}", response_model=HistoryResponse)
def history(session_id: str) -> HistoryResponse:
    """Replay a conversation, so a reload does not look like amnesia."""
    user = db_service.get_or_create_user(web_session_id=session_id)
    conversation_id = db_service.get_or_create_conversation(user["id"])
    return HistoryResponse(
        session_id=session_id,
        messages=db_service.get_history(conversation_id, limit=100),
    )


@router.get("/profile/{session_id}", response_model=ProfileResponse)
def profile(session_id: str) -> ProfileResponse:
    """What F4 has learned about this runner, for the panel to show.

    Read-only on purpose. There is no PUT and no form behind this: the runner
    never types their profile in, they mention it and the coach hears it. An
    editable profile would be a second source of truth competing with the
    conversation, and the conversation has to win.
    """
    user = db_service.get_or_create_user(web_session_id=session_id)
    return ProfileResponse(
        session_id=session_id,
        username=user["username"],
        goal=user["goal"],
        experience_level=user["experience_level"],
        weekly_km=user["weekly_km"],
        race_date=user["race_date"],
        weeks_to_race=weeks_until(user["race_date"], datetime.now(timezone.utc).date()),
    )
