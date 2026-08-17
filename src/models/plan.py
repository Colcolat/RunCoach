"""The week the coach laid out.

One row per runner, replaced whenever a new week is given. There is no history
of past weeks because nothing reads one: the panel shows what to do now, and
what the coach said three weeks ago is already in the conversation.

The sessions are a JSON column rather than a table of their own. They are always
written together and always read together - no query ever asks "who runs on
Tuesdays" - so rows would buy joins and a migration and pay back nothing. The
shape is fixed and validated in src.coaching.plan before it ever arrives here.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, utcnow


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Unique: a runner has one current week, and asking for a new plan replaces
    # it rather than adding a second one the panel would have to choose between.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # [{"day": 1-7, "km": float, "note": str | None}, ...] in day order.
    sessions: Mapped[list] = mapped_column(JSON, default=list)

    # Shown as "actualizado hace un rato", so a runner can tell a plan given
    # today from one that has been sitting there since last week.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="plan")


from src.models.user import User  # noqa: E402  (resolves the annotation)
