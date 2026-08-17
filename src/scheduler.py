"""The sweep that makes the coach act without being asked.

One recurring job, not one job per reminder. APScheduler is only a clock here:
it asks `run_due_reminders` every minute, and everything that decides anything
lives in the database and in src.coaching.reminders.

That is what makes restarts uninteresting. There is no scheduler state to
persist, because the state was never in the scheduler.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.coaching.reminders import (
    daily_is_due,
    daily_message,
    days_silent,
    inactivity_is_due,
    inactivity_message,
    plan_is_due,
    plan_message,
    session_on,
)
from src.config import get_settings
from src.services import db_service
from src.services.telegram_service import TelegramService, TelegramUnavailableError

logger = logging.getLogger(__name__)


def _timezone() -> ZoneInfo:
    """The zone daily reminders are read in, falling back rather than crashing.

    A misconfigured zone name should cost correct local times, not the whole
    reminder system.
    """
    name = get_settings().reminder_timezone
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any zoneinfo failure means the same thing
        logger.error("Unknown reminder timezone %r; falling back to UTC", name)
        return ZoneInfo("UTC")


async def run_due_reminders(telegram: TelegramService) -> int:
    """Send every reminder that is due right now. Returns how many went out.

    Never raises. This runs on a timer with nobody watching, and an exception
    escaping here would kill the job and take every future reminder with it,
    silently, until someone read the logs.
    """
    settings = get_settings()
    if not settings.reminders_enabled:
        return 0

    now = datetime.now(timezone.utc)
    tz = _timezone()
    sent = 0

    try:
        candidates = db_service.pending_reminders()
    except Exception:  # noqa: BLE001
        logger.exception("Could not read pending reminders")
        return 0

    for row in candidates:
        try:
            text = _due_text(row, now, tz, settings)
            if text is None:
                continue

            await telegram.send(row["telegram_id"], text)
            db_service.mark_reminder_sent(row["id"], now)
            sent += 1
        except TelegramUnavailableError:
            # A blocked bot or a dead chat is one runner's problem, not the
            # sweep's. Leaving last_sent_at alone means it retries next time.
            logger.warning("Could not deliver reminder %s", row["id"])
        except Exception:  # noqa: BLE001 - one bad row must not stop the rest
            logger.exception("Reminder %s failed", row["id"])

    if sent:
        logger.info("Sent %d reminder(s)", sent)
    return sent


def _local_date(now: datetime, tz: ZoneInfo) -> date:
    """Today in the runner's zone, which is the only "today" a reminder means."""
    return now.astimezone(tz).date()


def _due_text(row: dict, now: datetime, tz: ZoneInfo, settings) -> str | None:
    """The message this reminder should send now, or None if it is not due."""
    if row["kind"] == "daily":
        if daily_is_due(row["at_time"], row["last_sent_at"], now, tz):
            return daily_message(row["profile"])
        return None

    # The two training-day kinds differ only in which day they speak about, so
    # they share everything except that offset.
    if row["kind"] in ("plan_today", "plan_eve"):
        days_ahead = 1 if row["kind"] == "plan_eve" else 0
        sessions = row.get("sessions") or []
        if not plan_is_due(
            row["at_time"], row["last_sent_at"], now, tz, sessions, days_ahead
        ):
            return None

        target = _local_date(now, tz) + timedelta(days=days_ahead)
        session = session_on(sessions, target)
        # plan_is_due already established there is one; this keeps the type
        # honest rather than trusting that across two calls.
        if session is None:
            return None
        return plan_message(session, row["profile"], days_ahead)

    if row["kind"] == "inactivity":
        if inactivity_is_due(
            row["last_seen_at"],
            row["last_sent_at"],
            now,
            settings.inactivity_after_days,
            settings.inactivity_cooldown_days,
        ):
            return inactivity_message(days_silent(row["last_seen_at"], now), row["profile"])
        return None

    logger.warning("Unknown reminder kind %r on row %s", row["kind"], row["id"])
    return None


def build_scheduler(telegram: TelegramService) -> AsyncIOScheduler:
    """One job, on the application's own event loop."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_due_reminders,
        trigger="interval",
        seconds=get_settings().reminder_sweep_seconds,
        args=[telegram],
        id="reminder-sweep",
        # If the process was busy or asleep, run once on catching up rather
        # than once per missed tick.
        coalesce=True,
        max_instances=1,
    )
    return scheduler
