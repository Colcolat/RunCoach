"""The runner.

Identified per channel rather than by login. The web client holds a session id
in localStorage; Telegram supplies a chat id in F6. Either one resolves to the
same kind of row, which is what lets one runner exist across both.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Exactly one of these identifies a runner. Both are indexed and unique;
    # neither is required, because a web visitor has no Telegram id and vice
    # versa.
    web_session_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, index=True, default=None
    )

    username: Mapped[str | None] = mapped_column(String(100), default=None)

    # The coaching profile. All optional: it is filled in from conversation as
    # the runner mentions things, which is F4.
    goal: Mapped[str | None] = mapped_column(String(20), default=None)
    experience_level: Mapped[str | None] = mapped_column(String(20), default=None)
    weekly_km: Mapped[float | None] = mapped_column(default=None)
    race_date: Mapped[str | None] = mapped_column(String(10), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # One current week, not a list: asking for a new plan replaces it.
    plan: Mapped["TrainingPlan | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


from src.models.conversation import Conversation  # noqa: E402  (resolves the annotation)
from src.models.reminder import Reminder  # noqa: E402  (resolves the annotation)
from src.models.plan import TrainingPlan  # noqa: E402  (resolves the annotation)
