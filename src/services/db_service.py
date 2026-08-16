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
from sqlalchemy.orm import Session

from src.database import session_scope
from src.models import ROLES, Base, Conversation, Message, Reminder, User
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


def clear_conversation(conversation_id: int) -> int:
    """Delete every message in a conversation. Returns how many were removed."""
    with session_scope() as session:
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id)
        ).all()
        for message in messages:
            session.delete(message)
        return len(messages)
