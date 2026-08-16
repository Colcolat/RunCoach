"""When a reminder is due, and what it says.

Domain logic, like prompts.py and extraction.py: no scheduler, no SDK, no I/O.
Deciding "is this due?" is the whole of F6 that can be got wrong quietly, so it
is a pure function of three timestamps and gets tested without a clock running.

The text is written here rather than generated. A reminder fires when nobody is
looking, often at seven in the morning, and a model call at that moment can fail
or cost quota that the runner would rather spend on an actual conversation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# How late a daily reminder may still be sent. The sweep only runs while the
# process is up, so a restart at nine can find a seven o'clock reminder still
# unsent; delivering it then is useful. Delivering it at eleven at night is not,
# and that is what this bounds.
DAILY_GRACE = timedelta(hours=2)


def parse_at_time(value: str | None) -> time | None:
    """Read "07:00" into a time. Returns None for anything unusable.

    The column is text, so it can hold whatever an earlier version wrote, and
    a malformed value must skip the reminder rather than break the sweep for
    every other runner in it.
    """
    if not value:
        return None
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def _local(moment: datetime, tz: ZoneInfo) -> datetime:
    """Move an instant into the reminder timezone, assuming UTC if it is naive.

    Naive datetimes come out of SQLite, which does not keep the offset. They
    were written as UTC, so that is what they are read back as.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(tz)


def daily_is_due(
    at_time: str | None,
    last_sent_at: datetime | None,
    now: datetime,
    tz: ZoneInfo,
) -> bool:
    """Whether today's reminder should go out at this moment.

    Due when the local wall clock has reached the requested time, it is not yet
    past the grace window, and nothing has been sent today. "Today" is decided
    in the runner's timezone, not in UTC, or a reminder at seven in the morning
    would be a different day's reminder for half the world.
    """
    wanted = parse_at_time(at_time)
    if wanted is None:
        return False

    local_now = _local(now, tz)
    scheduled = datetime.combine(local_now.date(), wanted, tzinfo=tz)

    if local_now < scheduled:
        return False
    if local_now - scheduled > DAILY_GRACE:
        return False

    if last_sent_at is not None and _local(last_sent_at, tz).date() == local_now.date():
        return False

    return True


def inactivity_is_due(
    last_seen_at: datetime | None,
    last_sent_at: datetime | None,
    now: datetime,
    after_days: int,
    cooldown_days: int,
) -> bool:
    """Whether a runner who has gone quiet should be nudged.

    The cooldown is the point of this one. Without it the same silence produces
    a nudge every single sweep, which is how a helpful reminder becomes the
    reason someone blocks the bot.
    """
    if last_seen_at is None:
        return False

    silent_for = now - _as_utc(last_seen_at)
    if silent_for < timedelta(days=after_days):
        return False

    if last_sent_at is not None:
        since_last = now - _as_utc(last_sent_at)
        if since_last < timedelta(days=cooldown_days):
            return False

    return True


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment


def days_silent(last_seen_at: datetime | None, now: datetime) -> int:
    if last_seen_at is None:
        return 0
    return max(0, (now - _as_utc(last_seen_at)).days)


# --- what the reminder says --------------------------------------------------

def _km(value) -> str:
    """Render kilometres the way a person says them: 3, not 3.0.

    The column is a float because half-kilometres are real, but a whole number
    that arrives as 3.0 reads like a database leaked onto a lock screen.
    """
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def daily_message(profile: dict | None = None) -> str:
    """The morning nudge, written to be useful even with an empty profile."""
    profile = profile or {}
    goal = profile.get("goal")
    weekly = profile.get("weekly_km")
    if weekly is not None:
        weekly = _km(weekly)

    if goal and weekly:
        return (
            f"Buenos días. Toca entrenar para tu {goal}. Vas por {weekly} kilómetros "
            "a la semana, así que hoy suma lo tuyo sin prisa. Cuéntame cómo te fue "
            "cuando termines."
        )
    if goal:
        return (
            f"Buenos días. Hoy toca entrenar para tu {goal}. Si me dices cuánto "
            "corres a la semana, te armo el plan de estos días."
        )
    return (
        "Buenos días. Hoy toca salir a correr. Cuéntame qué quieres preparar y "
        "cuánto estás corriendo por semana, y armamos tu plan."
    )


def inactivity_message(days: int, profile: dict | None = None) -> str:
    """The nudge after a silence, which has to land as concern, not as nagging."""
    profile = profile or {}
    goal = profile.get("goal")

    when = "unos días" if days < 7 else f"{days} días"
    tail = f" Si sigues con el {goal}, retomamos donde lo dejamos." if goal else ""

    return (
        f"Hace {when} que no hablamos. No pasa nada por parar, pero cuanto antes "
        f"vuelvas, menos base pierdes.{tail} ¿Cómo vas?"
    )
