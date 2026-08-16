"""Text chat endpoints.

Every request carries a session id. The browser mints one on first visit and
keeps it in localStorage, so a returning runner keeps their history with no
login. It is not authenticated, which is fine for a demo and stated as a
limitation in the README.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.agents.coach_agent import CoachAgent
from src.services.telegram_service import TelegramService
from src.coaching.prompts import weeks_until
from src.dependencies import get_coach, get_limiter, get_telegram
from src.ratelimit import TurnLimiter
from src.services import db_service

router = APIRouter(prefix="/api", tags=["chat"])

# The browser mints 32 hex characters, but the shape is not what matters here:
# the bound is. These endpoints used to accept a string of any length and create
# a row for it, so two unauthenticated GETs were unbounded writes. A probe with
# a 5000-character id was accepted and stored.
#
# Deliberately looser than the browser's own format so that a session id remains
# something a caller can choose, which is what makes the API usable from a
# script or a test without a special case.
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _known_session(session_id: str) -> dict | None:
    """The runner behind this id, or None. Never creates one.

    The important half is that this cannot write. A read that creates rows lets
    anyone fill the disk by asking about ids that never existed.

    Returning None for both "malformed" and "never seen" is deliberate: the
    caller renders an empty profile either way, and distinguishing them would
    turn this into an oracle for which session ids exist.
    """
    if not SESSION_ID.match(session_id):
        return None
    return db_service.find_user(web_session_id=session_id)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    # Same bound and charset as the read paths. The write path always cost a
    # model call, so it was never the cheap way to make rows, but an id is a
    # primary key in everything but name and should not accept arbitrary bytes.
    session_id: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
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

    # F6. Travels with the profile so the panel needs one request, not two.
    # `telegram_url` is null when no bot username is configured, and the client
    # hides the button rather than offering a broken t.me address.
    telegram_linked: bool = False
    telegram_url: str | None = None
    reminder_at: str | None = None


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
    payload: ChatRequest,
    coach: CoachAgent = Depends(get_coach),
    limiter: TurnLimiter = Depends(get_limiter),
) -> ChatResponse:
    session_id = payload.session_id or uuid.uuid4().hex

    # A turn can cost two requests from a 500-a-day budget. Refusing here is
    # what stops one caller emptying it and leaving the next visitor with a
    # coach that cannot answer. 429 rather than a degraded reply, because this
    # is our limit and not the model's, and the caller can act on it.
    if not limiter.allow(session_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="demasiadas preguntas seguidas; espera unos segundos",
        )

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
    """Replay a conversation, so a reload does not look like amnesia.

    A session nobody has spoken to has no history, and asking about it must not
    bring one into existence.
    """
    user = _known_session(session_id)
    if user is None:
        return HistoryResponse(session_id=session_id, messages=[])

    conversation_id = db_service.get_or_create_conversation(user["id"])
    return HistoryResponse(
        session_id=session_id,
        messages=db_service.get_history(conversation_id, limit=100),
    )


@router.get("/profile/{session_id}", response_model=ProfileResponse)
def profile(
    session_id: str, telegram: TelegramService = Depends(get_telegram)
) -> ProfileResponse:
    """What F4 has learned about this runner, for the panel to show.

    Read-only on purpose. There is no PUT and no form behind this: the runner
    never types their profile in, they mention it and the coach hears it. An
    editable profile would be a second source of truth competing with the
    conversation, and the conversation has to win.
    """
    user = _known_session(session_id)
    if user is None:
        # A first visit is legitimately empty, and so is a made-up id. Both get
        # the same empty panel, and neither leaves a row behind.
        return ProfileResponse(
            session_id=session_id, telegram_url=telegram.deep_link(session_id)
        )

    return ProfileResponse(
        session_id=session_id,
        username=user["username"],
        goal=user["goal"],
        experience_level=user["experience_level"],
        weekly_km=user["weekly_km"],
        race_date=user["race_date"],
        weeks_to_race=weeks_until(user["race_date"], datetime.now(timezone.utc).date()),
        telegram_linked=user["telegram_id"] is not None,
        telegram_url=telegram.deep_link(session_id),
        reminder_at=db_service.daily_reminder_time(user["id"]),
    )
