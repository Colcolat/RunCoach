"""When a reminder is due.

Pure functions of three timestamps, so none of this needs a scheduler, a clock
or a bot. This is the part of F6 that can be wrong quietly: a reminder that
never fires looks like a reminder nobody set, and one that fires every sweep
looks like a bug in Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.coaching.reminders import (
    daily_is_due,
    daily_message,
    days_silent,
    inactivity_is_due,
    inactivity_message,
    parse_at_time,
)

MADRID = ZoneInfo("Europe/Madrid")
UTC = ZoneInfo("UTC")


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# --- reading the stored time -------------------------------------------------

def test_a_stored_time_is_read_back():
    assert parse_at_time("07:00").hour == 7


@pytest.mark.parametrize("stored", [None, "", "por la mañana", "25:00", "7"])
def test_an_unusable_time_is_refused_rather_than_raised(stored):
    """The column is text and the sweep runs for everyone: one bad row must not
    take down the reminders of every other runner in the same pass."""
    assert parse_at_time(stored) is None


# --- the daily reminder ------------------------------------------------------

def test_it_fires_once_the_local_clock_reaches_the_hour():
    # 07:00 in Madrid is 05:00 UTC in August.
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 5, 0), MADRID) is True


def test_it_does_not_fire_before_the_hour():
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 4, 30), MADRID) is False


def test_the_hour_is_read_in_the_runners_timezone_not_in_utc():
    """The same instant, two timezones, two answers. That is the whole point:
    "seven in the morning" is a wall clock, and a wall clock needs a place."""
    instant = utc(2026, 8, 20, 5, 0)  # 07:00 in Madrid, 05:00 in UTC

    assert daily_is_due("07:00", None, instant, MADRID) is True
    assert daily_is_due("07:00", None, instant, UTC) is False


def test_the_grace_window_ends_where_it_says_it_does():
    """Exactly two hours late still goes out; a minute later does not."""
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 7, 0), MADRID) is True
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 7, 1), MADRID) is False


def test_a_reminder_missed_while_the_process_was_down_still_arrives():
    """Restarting at 08:00 should still deliver the 07:00 nudge; it is useful late."""
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 6, 30), MADRID) is True


def test_but_not_hours_late():
    """A run reminder at eleven at night is worse than no reminder."""
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 20, 0), MADRID) is False


def test_it_does_not_fire_twice_in_one_day():
    already = utc(2026, 8, 20, 5, 1)

    assert daily_is_due("07:00", already, utc(2026, 8, 20, 5, 30), MADRID) is False


def test_it_fires_again_the_next_day():
    yesterday = utc(2026, 8, 19, 5, 1)

    assert daily_is_due("07:00", yesterday, utc(2026, 8, 20, 5, 0), MADRID) is True


def test_a_naive_timestamp_is_read_as_utc():
    """SQLite drops the offset, so this is what comes back out of the database."""
    naive = datetime(2026, 8, 20, 5, 1)

    assert daily_is_due("07:00", naive, utc(2026, 8, 20, 5, 30), MADRID) is False


def test_no_time_set_means_no_daily_reminder():
    assert daily_is_due(None, None, utc(2026, 8, 20, 5, 0), MADRID) is False


# --- the inactivity nudge ----------------------------------------------------

def test_a_runner_who_has_gone_quiet_is_nudged():
    seen = utc(2026, 8, 10, 9, 0)

    assert inactivity_is_due(seen, None, utc(2026, 8, 20, 9, 0), 3, 7) is True


def test_someone_who_spoke_yesterday_is_left_alone():
    seen = utc(2026, 8, 19, 9, 0)

    assert inactivity_is_due(seen, None, utc(2026, 8, 20, 9, 0), 3, 7) is False


def test_the_cooldown_stops_the_same_silence_nudging_every_sweep():
    """Without this the nudge repeats every minute, which is how someone blocks a bot."""
    seen = utc(2026, 8, 1, 9, 0)
    sent = utc(2026, 8, 19, 9, 0)

    assert inactivity_is_due(seen, sent, utc(2026, 8, 20, 9, 0), 3, 7) is False


def test_a_long_enough_silence_earns_a_second_nudge():
    seen = utc(2026, 8, 1, 9, 0)
    sent = utc(2026, 8, 5, 9, 0)

    assert inactivity_is_due(seen, sent, utc(2026, 8, 20, 9, 0), 3, 7) is True


def test_a_runner_who_never_appeared_is_not_chased():
    assert inactivity_is_due(None, None, utc(2026, 8, 20, 9, 0), 3, 7) is False


def test_the_silence_is_counted_in_whole_days():
    assert days_silent(utc(2026, 8, 10, 9, 0), utc(2026, 8, 20, 9, 0)) == 10
    assert days_silent(None, utc(2026, 8, 20, 9, 0)) == 0


# --- what it says ------------------------------------------------------------

def test_the_daily_message_uses_the_profile_when_there_is_one():
    text = daily_message({"goal": "10K", "weekly_km": 15.0})

    assert "10K" in text
    assert "15" in text


def test_the_daily_message_still_works_with_nothing_on_record():
    """It fires at seven in the morning whether or not F4 has learned anything."""
    text = daily_message({})

    assert len(text) > 40
    assert "None" not in text


def test_the_inactivity_message_names_the_silence():
    assert "12 días" in inactivity_message(12)


def test_a_short_silence_is_not_counted_out_loud():
    """"Hace 4 días que no hablamos" reads as surveillance; "unos días" does not."""
    assert "unos días" in inactivity_message(4)


@pytest.mark.parametrize("builder", [daily_message, lambda p: inactivity_message(9, p)])
def test_no_reminder_ever_shouts_in_markdown(builder):
    """These are read in Telegram and may be spoken back later; same rule as the coach."""
    text = builder({"goal": "21K", "weekly_km": 30.0})

    for forbidden in ("*", "_", "#", "- ", "1."):
        assert forbidden not in text


# --- how numbers read --------------------------------------------------------

def test_a_whole_number_of_kilometres_reads_as_a_whole_number():
    """Seen on a real lock screen: "Vas por 3.0 kilómetros a la semana". The
    column is a float because half-kilometres are real, but 3.0 in a push
    notification reads like a database leaked into the product."""
    assert "3 kilómetros" in daily_message({"goal": "Maratón", "weekly_km": 3.0})
    assert "3.0" not in daily_message({"goal": "Maratón", "weekly_km": 3.0})


def test_a_half_kilometre_survives():
    """The rounding must not lie: 7.5 is a real weekly volume."""
    assert "7.5 kilómetros" in daily_message({"goal": "10K", "weekly_km": 7.5})
