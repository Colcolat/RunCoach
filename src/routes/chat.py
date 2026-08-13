"""Text chat endpoints.

Every request carries a session id. The browser mints one on first visit and
keeps it in localStorage, so a returning runner keeps their history with no
login. It is not authenticated, which is fine for a demo and stated as a
limitation in the README.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.agents.coach_agent import CoachAgent
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
