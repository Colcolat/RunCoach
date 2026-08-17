"""Persistence.

Two rules hold this module together.

Sessions never escape. Every method opens its own scope and returns plain dicts
and integers. Returning an ORM instance would let a caller touch an attribute
after the session closed and raise DetachedInstanceError, which is the kind of
bug that appears only under load and far from its cause.

Operations are synchronous. SQLite serialises writes anyway, and FastAPI runs
synchronous dependencies in a threadpool, so the event loop is not blocked. The
signatures then tell the truth about what suspends and what does not.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.database import session_scope
from src.models import ROLES, Base, Conversation, Message, Reminder, TrainingPlan, User
from src.models.base import utcnow
from src.database import get_engine

logger = logging.getLogger(__name__)

PROFILE_FIELDS = ("username", "goal", "experience_level", "weekly_km", "race_date")


def create_schema() -> None:
    """Create any missing tables.

    Enough while the schema only grows. A column change would need Alembic, and
    that is called out in the README rather than pretended away.
    """
    Base.metadata.create_all(get_engine())


def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "web_session_id": user.web_session_id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "goal": user.goal,
        "experience_level": user.experience_level,
        "weekly_km": user.weekly_km,
        "race_date": user.race_date,
    }


def _resolve(session: Session, web_session_id: str | None, telegram_id: int | None) -> User:
    if web_session_id:
        stmt = select(User).where(User.web_session_id == web_session_id)
    elif telegram_id is not None:
        stmt = select(User).where(User.telegram_id == telegram_id)
    else:
        raise ValueError("a runner needs either a web_session_id or a telegram_id")

    user = session.scalars(stmt).first()
    if user is None:
        user = User(web_session_id=web_session_id, telegram_id=telegram_id)
        session.add(user)
        session.flush()  # assigns the primary key without ending the transaction
    return user


def find_user(
    web_session_id: str | None = None, telegram_id: int | None = None
) -> dict | None:
    """Look a runner up without creating one.

    The read paths need this. get_or_create_user on a GET turned every request
    into a write, so anyone could fill the disk by asking about session ids that
    never existed.
    """
    with session_scope() as session:
        if web_session_id:
            stmt = select(User).where(User.web_session_id == web_session_id)
        elif telegram_id is not None:
            stmt = select(User).where(User.telegram_id == telegram_id)
        else:
            return None
        user = session.scalars(stmt).first()
        return _user_to_dict(user) if user is not None else None


def get_or_create_user(
    web_session_id: str | None = None,
    telegram_id: int | None = None,
    username: str | None = None,
) -> dict:
    with session_scope() as session:
        user = _resolve(session, web_session_id, telegram_id)
        if username and not user.username:
            user.username = username
        return _user_to_dict(user)


def update_profile(user_id: int, **fields) -> dict:
    """Update known profile fields, ignoring anything unrecognised.

    Filtered rather than passed through, because F4 fills this from model
    output and a hallucinated key must not become a column write.
    """
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            raise ValueError(f"no user with id {user_id}")
        for key, value in fields.items():
            if key in PROFILE_FIELDS and value is not None:
                setattr(user, key, value)
        return _user_to_dict(user)


def get_or_create_conversation(user_id: int) -> int:
    """Return the id of this runner's open conversation, creating one if needed.

    An id rather than the object: the row is useless to the caller once the
    session closes, and the id is all any of them actually use.
    """
    with session_scope() as session:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.id.desc())
        )
        conversation = session.scalars(stmt).first()
        if conversation is None:
            conversation = Conversation(user_id=user_id)
            session.add(conversation)
            session.flush()
        return conversation.id


def save_message(
    conversation_id: int, role: str, content: str, channel: str = "web"
) -> int:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")

    with session_scope() as session:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            channel=channel,
        )
        session.add(message)
        session.flush()
        return message.id


def get_history(conversation_id: int, limit: int = 20) -> list[dict[str, str]]:
    """The last `limit` turns, oldest first.

    The window is taken from the end and then reversed, so a long conversation
    keeps its most recent turns rather than its first ones.
    """
    with session_scope() as session:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list(session.scalars(stmt))
        rows.reverse()
        return [
            {"role": row.role, "content": row.content, "channel": row.channel}
            for row in rows
        ]


def link_telegram(web_session_id: str, telegram_id: int, username: str | None = None) -> dict:
    """Attach a Telegram chat to the runner who already exists on the web.

    This is what makes a reminder reach someone who never left the browser. The
    web session id travels in the /start deep link, so the runner taps once and
    the two channels become one person rather than two rows that happen to be
    the same human.

    If that Telegram id was already attached to a different row, the link moves
    rather than failing: the column is unique, and a runner re-linking from a
    second browser is a likelier explanation than an attack. The session id is
    not authenticated and never was, which the README says plainly.
    """
    with session_scope() as session:
        target = _resolve(session, web_session_id, None)

        existing = session.scalars(
            select(User).where(User.telegram_id == telegram_id)
        ).first()
        if existing is not None and existing.id != target.id:
            existing.telegram_id = None
            session.flush()

        target.telegram_id = telegram_id
        if username and not target.username:
            target.username = username
        return _user_to_dict(target)


def set_daily_reminder(user_id: int, at_time: str) -> int:
    """Set, or move, the one daily reminder this runner has.

    One per runner rather than many: "recuérdame a las siete" replaces the
    previous time instead of adding a second alarm, which is what someone means
    when they say it.
    """
    with session_scope() as session:
        stmt = select(Reminder).where(
            Reminder.user_id == user_id, Reminder.kind == "daily"
        )
        reminder = session.scalars(stmt).first()
        if reminder is None:
            reminder = Reminder(user_id=user_id, kind="daily")
            session.add(reminder)
        reminder.at_time = at_time
        reminder.enabled = True
        session.flush()
        return reminder.id


def set_plan_reminder(user_id: int, at_time: str, *, eve: bool = False) -> int:
    """Set, or move, this runner's training-day reminder.

    One of each kind, and setting one clears the other: "recuérdame la víspera"
    after "recuérdame por la mañana" is a change of mind, not a request for two
    messages about the same run.
    """
    kind = "plan_eve" if eve else "plan_today"
    other = "plan_today" if eve else "plan_eve"

    with session_scope() as session:
        for row in session.scalars(
            select(Reminder).where(Reminder.user_id == user_id, Reminder.kind == other)
        ):
            row.enabled = False

        stmt = select(Reminder).where(
            Reminder.user_id == user_id, Reminder.kind == kind
        )
        reminder = session.scalars(stmt).first()
        if reminder is None:
            reminder = Reminder(user_id=user_id, kind=kind)
            session.add(reminder)
        reminder.at_time = at_time
        reminder.enabled = True
        session.flush()
        return reminder.id


def plan_reminder(user_id: int) -> dict | None:
    """This runner's training-day reminder, if they set one."""
    with session_scope() as session:
        stmt = select(Reminder).where(
            Reminder.user_id == user_id,
            Reminder.kind.in_(("plan_today", "plan_eve")),
            Reminder.enabled.is_(True),
        )
        reminder = session.scalars(stmt).first()
        if reminder is None:
            return None
        return {"at_time": reminder.at_time, "eve": reminder.kind == "plan_eve"}


def ensure_inactivity_reminder(user_id: int) -> int:
    """Every runner gets one, created the first time they are seen."""
    with session_scope() as session:
        stmt = select(Reminder).where(
            Reminder.user_id == user_id, Reminder.kind == "inactivity"
        )
        reminder = session.scalars(stmt).first()
        if reminder is None:
            reminder = Reminder(user_id=user_id, kind="inactivity")
            session.add(reminder)
            session.flush()
        return reminder.id


def pending_reminders() -> list[dict]:
    """Every enabled reminder for a runner who can actually be reached.

    Filtered on telegram_id here rather than in the sweep, because a reminder
    for someone with nowhere to send it is not a candidate, it is noise. The
    decision about whether each one is *due* stays in src.coaching.reminders,
    where it is a pure function and gets tested without a clock.
    """
    with session_scope() as session:
        stmt = (
            select(Reminder, User)
            .join(User, Reminder.user_id == User.id)
            .where(Reminder.enabled.is_(True), User.telegram_id.is_not(None))
            # Without this, reading user.plan below lazy-loads one row at a
            # time: measured at twenty reminders it was twenty-one queries,
            # twenty of them to training_plans. The sweep runs every minute and
            # touches every reminder in the system, so it is the one query in
            # this module whose shape actually scales with users.
            .options(joinedload(User.plan))
        )
        return [
            {
                "id": reminder.id,
                "kind": reminder.kind,
                "at_time": reminder.at_time,
                "last_sent_at": reminder.last_sent_at,
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "last_seen_at": user.last_seen_at,
                "profile": _user_to_dict(user),
                # Only the plan kinds read this, but it is loaded here so the
                # sweep stays a pure decision over the rows it was handed
                # rather than reaching back into the database mid-loop.
                "sessions": (
                    list(user.plan.sessions)
                    if user.plan is not None and isinstance(user.plan.sessions, list)
                    else []
                ),
            }
            for reminder, user in session.execute(stmt).all()
        ]


def daily_reminder_time(user_id: int) -> str | None:
    """The time this runner asked to be reminded at, if they asked."""
    with session_scope() as session:
        stmt = select(Reminder).where(
            Reminder.user_id == user_id,
            Reminder.kind == "daily",
            Reminder.enabled.is_(True),
        )
        reminder = session.scalars(stmt).first()
        return reminder.at_time if reminder is not None else None


def mark_reminder_sent(reminder_id: int, when: datetime) -> None:
    with session_scope() as session:
        reminder = session.get(Reminder, reminder_id)
        if reminder is not None:
            reminder.last_sent_at = when


def touch_last_seen(user_id: int) -> None:
    """Record that the runner is still around, which is what silence is measured from."""
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is not None:
            user.last_seen_at = utcnow()


# --- the week's plan (F9) -----------------------------------------------------

def save_plan(user_id: int, sessions: list[dict]) -> None:
    """Replace this runner's week.

    Refuses an empty list rather than storing it. An extraction that found
    nothing means the coach was not laying out a week this turn, which is not
    the same as the week being over, and wiping the panel because someone asked
    an unrelated question is the one failure mode a runner would actually
    notice.
    """
    if not sessions:
        return

    with session_scope() as session:
        plan = session.scalar(
            select(TrainingPlan).where(TrainingPlan.user_id == user_id)
        )
        if plan is None:
            session.add(TrainingPlan(user_id=user_id, sessions=sessions))
            return
        # Reassigned, never mutated in place. SQLAlchemy instruments the
        # attribute, so `plan.sessions = ...` is seen and written; appending to
        # the existing list is not, and is silently lost at commit. Measured
        # both ways rather than assumed. Keeping every write a reassignment is
        # what makes flag_modified unnecessary here.
        plan.sessions = sessions
        # updated_at moves itself: the column carries onupdate=utcnow, which
        # fires because the row above is now dirty. An explicit nudge here was
        # doing nothing but claiming it was needed.


def get_plan(user_id: int) -> list[dict]:
    """This runner's week, or an empty list when there is none yet."""
    with session_scope() as session:
        plan = session.scalar(
            select(TrainingPlan).where(TrainingPlan.user_id == user_id)
        )
        if plan is None or not isinstance(plan.sessions, list):
            return []
        return list(plan.sessions)
