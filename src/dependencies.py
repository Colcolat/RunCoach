"""Composition root.

Nothing here is constructed at import time. Providers are cached so a process
builds one of each, and tests replace them through
`app.dependency_overrides` rather than by monkeypatching module globals.
"""

from __future__ import annotations

from functools import lru_cache

from src.agents.coach_agent import CoachAgent
from src.services.gemini_service import GeminiService
from src.services.live_service import LiveVoiceService
from src.services.telegram_service import TelegramService


@lru_cache
def get_gemini() -> GeminiService:
    return GeminiService()


@lru_cache
def get_live() -> LiveVoiceService:
    return LiveVoiceService()


@lru_cache
def get_coach() -> CoachAgent:
    return CoachAgent(gemini=get_gemini())


@lru_cache
def get_telegram() -> TelegramService:
    return TelegramService()
