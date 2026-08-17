"""Linking a chat, and the sweep that delivers.

The sweep runs on a timer with nobody watching, which is what makes these worth
having: a reminder that never fires looks like a reminder nobody set, and one
that fires twice looks like a bug in Telegram.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.bot import handle_message, handle_start
from src.scheduler import run_due_reminders
from src.services import db_service

SESSION = "a" * 32
CHAT = 55501


@pytest.fixture(autouse=True)
def schema():
    db_service.create_schema()


def a_runner(session_id=SESSION, chat_id=CHAT, **profile) -> dict:
    user = db_service.get_or_create_user(web_session_id=session_id)
    if profile:
        db_service.update_profile(user["id"], **profile)
    if chat_id is not None:
        db_service.link_telegram(session_id, chat_id)
    db_service.ensure_inactivity_reminder(user["id"])
    return db_service.get_or_create_user(web_session_id=session_id)


# --- linking -----------------------------------------------------------------

def test_a_chat_joins_the_conversation_that_already_existed(client, gemini):
    """The whole reason for the deep link: not a second runner, the same one."""
    gemini.extraction = {"goal": "10K"}
    client.post("/api/chat", json={"message": "quiero un 10K", "session_id": SESSION})

    db_service.link_telegram(SESSION, CHAT)

    user = db_service.get_or_create_user(telegram_id=CHAT)
    assert user["goal"] == "10K"
    assert user["web_session_id"] == SESSION


def test_relinking_moves_the_chat_instead_of_failing():
    """telegram_id is unique, and re-linking from a second browser is likelier
    than an attack. Failing here would leave the runner stuck."""
    db_service.link_telegram(SESSION, CHAT)
    db_service.link_telegram("b" * 32, CHAT)

    assert db_service.get_or_create_user(web_session_id=SESSION)["telegram_id"] is None
    assert db_service.get_or_create_user(web_session_id="b" * 32)["telegram_id"] == CHAT


@pytest.mark.asyncio
async def test_opening_the_bot_with_no_link_does_not_create_a_second_runner():
    """Silently creating a fresh profile is exactly what the linking prevents."""
    reply = await handle_start("", CHAT, "Juan")

    assert "Conectar Telegram" in reply
    assert db_service.pending_reminders() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["no-es-una-sesion", "abc", "z" * 32, "1234"])
async def test_a_payload_that_is_not_ours_is_refused(payload):
    reply = await handle_start(payload, CHAT, None)

    assert "Conectar Telegram" in reply


@pytest.mark.asyncio
async def test_a_valid_link_also_arms_the_inactivity_reminder():
    await handle_start(SESSION, CHAT, "Juan")

    kinds = [row["kind"] for row in db_service.pending_reminders()]
    assert kinds == ["inactivity"]


@pytest.mark.asyncio
async def test_a_telegram_turn_reaches_the_same_coach(coach, gemini):
    reply = await handle_message(coach, CHAT, "corro 20 km por semana", "Juan")

    assert reply == gemini.reply
    user = db_service.get_or_create_user(telegram_id=CHAT)
    history = db_service.get_history(db_service.get_or_create_conversation(user["id"]))
    assert [m["channel"] for m in history] == ["telegram", "telegram"]


@pytest.mark.asyncio
async def test_an_empty_telegram_message_is_answered_not_crashed(coach):
    assert await handle_message(coach, CHAT, "   ", None)


# --- the daily reminder ------------------------------------------------------

@pytest.mark.asyncio
async def test_a_daily_reminder_goes_out_when_it_is_due(telegram, monkeypatch):
    user = a_runner(goal="10K", weekly_km=15.0)
    db_service.set_daily_reminder(user["id"], _in_a_minute())

    assert await run_due_reminders(telegram) == 1

    chat_id, text = telegram.sent[0]
    assert chat_id == CHAT
    assert "10K" in text


@pytest.mark.asyncio
async def test_it_does_not_go_out_twice(telegram):
    user = a_runner()
    db_service.set_daily_reminder(user["id"], _in_a_minute())

    assert await run_due_reminders(telegram) == 1
    assert await run_due_reminders(telegram) == 0


@pytest.mark.asyncio
async def test_a_reminder_for_another_hour_stays_put(telegram):
    user = a_runner()
    db_service.set_daily_reminder(user["id"], _hours_from_now(5))

    assert await run_due_reminders(telegram) == 0


@pytest.mark.asyncio
async def test_asking_again_moves_the_time_rather_than_adding_an_alarm():
    """"recuérdame a las siete" said twice is one reminder, not two."""
    user = a_runner()
    db_service.set_daily_reminder(user["id"], "07:00")
    db_service.set_daily_reminder(user["id"], "08:30")

    daily = [r for r in db_service.pending_reminders() if r["kind"] == "daily"]
    assert len(daily) == 1
    assert daily[0]["at_time"] == "08:30"
    assert db_service.daily_reminder_time(user["id"]) == "08:30"


# --- the inactivity nudge ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_silent_runner_is_nudged(telegram):
    user = a_runner(goal="21K")
    _went_quiet(user["id"], days=10)

    assert await run_due_reminders(telegram) == 1
    assert "21K" in telegram.sent[0][1]


@pytest.mark.asyncio
async def test_the_same_silence_does_not_nudge_every_sweep(telegram):
    """Without the cooldown this fires once a minute, forever."""
    user = a_runner()
    _went_quiet(user["id"], days=10)

    await run_due_reminders(telegram)
    await run_due_reminders(telegram)

    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_someone_who_was_here_today_is_left_alone(telegram):
    a_runner()

    assert await run_due_reminders(telegram) == 0


# --- the sweep survives things -----------------------------------------------

@pytest.mark.asyncio
async def test_a_runner_with_no_chat_is_not_a_candidate(telegram):
    """A reminder nobody can receive is noise, not work."""
    user = db_service.get_or_create_user(web_session_id="sin-telegram")
    db_service.ensure_inactivity_reminder(user["id"])
    _went_quiet(user["id"], days=30)

    assert await run_due_reminders(telegram) == 0
    assert db_service.pending_reminders() == []


@pytest.mark.asyncio
async def test_a_failed_delivery_is_retried_next_sweep(telegram):
    """Leaving last_sent_at alone is what makes the retry happen."""
    from src.services.telegram_service import TelegramUnavailableError

    user = a_runner()
    _went_quiet(user["id"], days=10)
    telegram.fail_with = TelegramUnavailableError("blocked")

    assert await run_due_reminders(telegram) == 0

    telegram.fail_with = None
    assert await run_due_reminders(telegram) == 1


@pytest.mark.asyncio
async def test_the_sweep_never_raises(telegram, monkeypatch):
    """An exception escaping here kills the job and every future reminder with it."""
    monkeypatch.setattr(
        db_service, "pending_reminders", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    assert await run_due_reminders(telegram) == 0


@pytest.mark.asyncio
async def test_reminders_can_be_switched_off(telegram, monkeypatch):
    from src.config import get_settings

    user = a_runner()
    _went_quiet(user["id"], days=10)
    monkeypatch.setenv("REMINDERS_ENABLED", "false")
    get_settings.cache_clear()

    assert await run_due_reminders(telegram) == 0


# --- helpers -----------------------------------------------------------------

def _in_a_minute() -> str:
    """A wall-clock time already reached in the configured zone."""
    from zoneinfo import ZoneInfo

    from src.config import get_settings

    tz = ZoneInfo(get_settings().reminder_timezone)
    now = datetime.now(tz)
    return now.strftime("%H:%M")


def _hours_from_now(hours: int) -> str:
    from zoneinfo import ZoneInfo

    from src.config import get_settings

    tz = ZoneInfo(get_settings().reminder_timezone)
    return (datetime.now(tz) + timedelta(hours=hours)).strftime("%H:%M")


def _went_quiet(user_id: int, days: int) -> None:
    """Backdate last_seen_at, which is what silence is measured from."""
    from src.database import session_scope
    from src.models import User

    with session_scope() as session:
        user = session.get(User, user_id)
        user.last_seen_at = datetime.now(timezone.utc) - timedelta(days=days)


# --- the deep link -----------------------------------------------------------

def test_the_deep_link_carries_the_session_so_nobody_types_a_code():
    from src.config import Settings
    from src.services.telegram_service import TelegramService

    service = TelegramService(
        settings=Settings(telegram_bot_token="t", telegram_bot_username="RunCoachBot")
    )

    assert service.deep_link(SESSION) == f"https://t.me/RunCoachBot?start={SESSION}"


def test_an_at_sign_in_the_configured_username_is_tolerated():
    from src.config import Settings
    from src.services.telegram_service import TelegramService

    service = TelegramService(
        settings=Settings(telegram_bot_token="t", telegram_bot_username="@RunCoachBot")
    )

    assert "t.me/RunCoachBot" in service.deep_link(SESSION)


def test_without_a_username_there_is_no_link_to_offer():
    """The client hides the button rather than offering a dead t.me address."""
    from src.config import Settings
    from src.services.telegram_service import TelegramService

    service = TelegramService(settings=Settings(telegram_bot_token="t"))

    assert service.deep_link(SESSION) is None


def test_no_token_means_the_bot_is_not_enabled():
    from src.config import Settings
    from src.services.telegram_service import TelegramService

    assert TelegramService(settings=Settings()).enabled is False


def test_health_reports_the_bot_without_probing_it(client):
    """Probing would spend someone else's rate limit on every health check."""
    components = client.get("/health").json()["components"]

    assert components["telegram"] == "not_configured"
    assert components["reminders"] == "on"


def test_a_missing_bot_does_not_make_the_application_unhealthy(client):
    """Otherwise a deployment without Telegram restart-loops instead of serving."""
    body = client.get("/health").json()

    assert body["status"] == "healthy"


def test_the_suite_can_never_start_a_real_bot():
    """Found by configuring one: Settings reads .env, so a developer with a real
    token had every app-building test polling Telegram over the network, and the
    suite went from nine seconds to a hundred and sixty. The fixture blanks it;
    this is what notices if that stops being true."""
    from src.config import get_settings

    assert get_settings().telegram_enabled is False


# --- being here counts ---------------------------------------------------------

@pytest.mark.asyncio
async def test_talking_counts_as_being_here(coach, gemini):
    """last_seen_at has onupdate=utcnow, which only fires when the user row
    changes - and an ordinary turn changes messages, not the runner. Without an
    explicit touch, someone chatting every morning with a profile that is
    already complete looks silent after three days."""
    from src.database import session_scope
    from src.models import User

    user = db_service.get_or_create_user(web_session_id=SESSION)
    _went_quiet(user["id"], days=10)

    # A turn that reveals nothing new, so no profile write happens.
    gemini.extraction = {}
    await coach.converse("vale, gracias", web_session_id=SESSION)

    with session_scope() as session:
        seen = session.get(User, user["id"]).last_seen_at
    assert (datetime.now(timezone.utc) - _aware(seen)).total_seconds() < 60


@pytest.mark.asyncio
async def test_an_active_runner_is_never_told_they_went_quiet(coach, gemini, telegram):
    """The bug this pair exists for, stated as the runner would experience it."""
    user = a_runner()
    _went_quiet(user["id"], days=10)

    gemini.extraction = {}
    await coach.converse("¿qué hago hoy?", web_session_id=SESSION)

    assert await run_due_reminders(telegram) == 0


def test_speaking_counts_too(coach):
    """Voice is a conversation, not a lesser channel."""
    from src.database import session_scope
    from src.models import User

    user = db_service.get_or_create_user(web_session_id=SESSION)
    _went_quiet(user["id"], days=10)

    coach.remember_voice_turn(SESSION, "user", "corro veinte kilómetros")

    with session_scope() as session:
        seen = session.get(User, user["id"]).last_seen_at
    assert (datetime.now(timezone.utc) - _aware(seen)).total_seconds() < 60


def _aware(moment):
    from zoneinfo import ZoneInfo
    return moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo("UTC"))


# --- reminders that follow the plan (F9) --------------------------------------
#
# The daily reminder fires every morning whether or not there is anything to do.
# These fire only on the days the week actually has a session, or the evening
# before one, which is what "recuérdame la rutina un día antes del día de
# ejercicio" asks for.

SEMANA = [
    {"day": 2, "km": 5.0, "note": "suaves"},    # martes
    {"day": 7, "km": 9.0, "note": "tirada larga"},  # domingo
]


def _at(day: str, hour: int = 7) -> datetime:
    """An instant on a named weekday, at that hour *in the runner's zone*.

    Built local and converted, not the other way round. A reminder set for seven
    in the morning means seven where the runner is, and an instant built in UTC
    lands at one in the morning in Mexico City - which is how the first version
    of these tests failed while the code was right.
    """
    from zoneinfo import ZoneInfo

    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    # 17 Aug 2026 is a Monday, so the index lands on the right weekday.
    local = datetime(
        2026, 8, 17 + dias.index(day), hour, 0,
        tzinfo=ZoneInfo("America/Mexico_City"),
    )
    return local.astimezone(timezone.utc)


def _entrenos(telegram) -> list[str]:
    """Only the training-day messages.

    The sweep runs every reminder a runner has, and freezing the clock days into
    the future also makes them look silent, so the inactivity nudge legitimately
    goes out alongside. Counting everything the sweep sent would make these
    tests about the sweep; they are about one kind of reminder.
    """
    return [text for _, text in telegram.sent if "toca entrenar" in text]


@pytest.mark.asyncio
async def test_a_training_day_reminder_fires_on_a_training_day(telegram, monkeypatch):
    user = a_runner(goal="10K")
    db_service.save_plan(user["id"], SEMANA)
    db_service.set_plan_reminder(user["id"], "07:00")

    monkeypatch.setattr("src.scheduler.datetime", _FrozenClock(_at("martes")))
    await run_due_reminders(telegram)

    assert len(_entrenos(telegram)) == 1
    assert "5 kilómetros" in _entrenos(telegram)[0]


@pytest.mark.asyncio
async def test_it_stays_quiet_on_a_rest_day(telegram, monkeypatch):
    """The whole difference from the daily reminder. A message on a rest day is
    the reason someone turns reminders off."""
    user = a_runner()
    db_service.save_plan(user["id"], SEMANA)
    db_service.set_plan_reminder(user["id"], "07:00")

    monkeypatch.setattr("src.scheduler.datetime", _FrozenClock(_at("miércoles")))
    await run_due_reminders(telegram)

    assert _entrenos(telegram) == []


@pytest.mark.asyncio
async def test_the_eve_reminder_speaks_about_tomorrow(telegram, monkeypatch):
    """"recuérdame la rutina un día antes del día de ejercicio", which is what
    was actually asked for."""
    user = a_runner(goal="10K")
    db_service.save_plan(user["id"], SEMANA)
    db_service.set_plan_reminder(user["id"], "21:00", eve=True)

    # Saturday evening, the night before Sunday's long run.
    monkeypatch.setattr("src.scheduler.datetime", _FrozenClock(_at("sábado", 21)))
    await run_due_reminders(telegram)

    enviados = _entrenos(telegram)
    assert len(enviados) == 1
    assert "Mañana" in enviados[0]
    assert "9 kilómetros" in enviados[0]


@pytest.mark.asyncio
async def test_the_eve_reminder_is_quiet_the_night_before_a_rest_day(telegram, monkeypatch):
    user = a_runner()
    db_service.save_plan(user["id"], SEMANA)
    db_service.set_plan_reminder(user["id"], "21:00", eve=True)

    # Sunday night: Monday is a rest day.
    monkeypatch.setattr("src.scheduler.datetime", _FrozenClock(_at("domingo", 21)))
    await run_due_reminders(telegram)

    assert _entrenos(telegram) == []


@pytest.mark.asyncio
async def test_a_runner_with_no_plan_gets_no_training_day_reminders(telegram, monkeypatch):
    """Nothing to remind them about yet, so nothing is sent - rather than a
    message that names no distance."""
    user = a_runner()
    db_service.set_plan_reminder(user["id"], "07:00")

    monkeypatch.setattr("src.scheduler.datetime", _FrozenClock(_at("martes")))
    await run_due_reminders(telegram)

    assert _entrenos(telegram) == []


def test_choosing_the_eve_replaces_the_same_day_reminder():
    """A change of mind, not a request for two messages about one run."""
    user = a_runner()
    db_service.set_plan_reminder(user["id"], "07:00")
    db_service.set_plan_reminder(user["id"], "21:00", eve=True)

    assert db_service.plan_reminder(user["id"]) == {"at_time": "21:00", "eve": True}
    activos = [r["kind"] for r in db_service.pending_reminders()]
    assert "plan_today" not in activos


class _FrozenClock:
    """Stops the sweep's clock on a chosen instant.

    The sweep reads `datetime.now(timezone.utc)`; only that needs replacing, and
    everything else on the class is passed straight through so the module keeps
    working normally.
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None):
        return self._moment.astimezone(tz) if tz else self._moment

    def __getattr__(self, name):
        return getattr(datetime, name)
