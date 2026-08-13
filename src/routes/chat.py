"""Text chat endpoints.

F1 is stateless: every request stands alone. F3 adds the session identifier and
replays history, at which point the request model grows a `session_id` and the
agent starts receiving real memory.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.agents.coach_agent import CoachAgent
from src.dependencies import get_coach

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    username: str | None = Field(default=None, max_length=100)


class ChatResponse(BaseModel):
    reply: str
    degraded: bool = False


class WelcomeResponse(BaseModel):
    greeting: str


@router.get("/welcome", response_model=WelcomeResponse)
def welcome(
    username: str | None = None, coach: CoachAgent = Depends(get_coach)
) -> WelcomeResponse:
    return WelcomeResponse(greeting=coach.welcome(username).text)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, coach: CoachAgent = Depends(get_coach)
) -> ChatResponse:
    try:
        reply = await coach.handle_message(payload.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return ChatResponse(reply=reply.text, degraded=reply.degraded)
