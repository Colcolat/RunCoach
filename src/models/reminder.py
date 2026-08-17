"""Reminders as rows, not as scheduled jobs.

APScheduler ships a SQLAlchemyJobStore that persists jobs, and it was the
obvious choice. It is not the one taken, for a reason worth stating: a persisted
APScheduler job is a pickled callable plus its arguments. That survives a
restart, but it does not survive a deploy that renames the function or changes
its signature, and it fails at trigger time rather than at import time, which is
the worst moment to find out.

So a reminder is a row of plain data, and the scheduler runs one recurring job
that asks "what is due?". Restarts are then uninteresting: the state was never
in the scheduler. The consequence is that the domain logic in
src.coaching.reminders can be tested without a scheduler running at all.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, utcnow

# What prompted the reminder. `daily` is the time the runner asked for out loud;
# `inactivity` is the coach noticing they have gone quiet. The two plan kinds
# (F9) are tied to the week in the panel: `plan_today` fires on the days that
# have a session, `plan_eve` fires the evening before one.
#
# Two kinds rather than one kind with an offset column, because the offset is
# the only thing that would differ and create_schema only creates missing
# tables - a new column would need a migration this project has deliberately not
# taken on. The cost is that "three days before" is not expressible, which
# nobody has asked for.
REMINDER_KINDS = ("daily", "inactivity", "plan_today", "plan_eve")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String(16))

    # Local wall-clock time as "HH:MM", for daily reminders only. Stored as text
    # because it is a time of day and not an instant: 07:00 means seven in the
    # morning every morning, which no timestamp can express.
    at_time: Mapped[str | None] = mapped_column(String(5), default=None)

    enabled: Mapped[bool] = mapped_column(default=True)

    # When this last went out. Both the daily "not twice in one day" rule and
    # the inactivity cooldown are decided from this one field, which is why
    # neither of them needs its own bookkeeping.
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="reminders")

    # The sweep always asks the same question: which enabled reminders exist,
    # of which kind. The index matches that, not the column order.
    __table_args__ = (Index("ix_reminders_enabled_kind", "enabled", "kind"),)


from src.models.user import User  # noqa: E402  (resolves the annotation)
