"""The week, from the coach's prose to the panel.

The plan was never data: it lived inside a reply, in sentences. These cover the
three places that can now go wrong - deciding a reply is worth reading, reading
it, and refusing to believe the result.
"""

from __future__ import annotations

import pytest

from src.coaching.plan import clean, looks_like_a_plan, week_total
from src.services import db_service


@pytest.fixture(autouse=True)
def schema():
    db_service.create_schema()


# --- the gate ----------------------------------------------------------------
#
# Every string here is a real reply from the deployed coach, kept verbatim.
# Guessing what the coach sounds like is how the regex parser looked fine and
# would have shipped wrong distances.

PLANES = [
    "Haz cuatro kilómetros suaves el martes, otros cuatro el jueves y el domingo la "
    "tirada larga de catorce kilómetros, descansando el resto de los días.",
    "Haz el martes diez kilómetros suaves, el jueves doce incluyendo series cortas de "
    "un minuto rápido, el sábado seis suaves y el domingo la tirada larga de dieciséis.",
    "Sal a trotar dos kilómetros el martes, otros dos el jueves y cierra con los dos "
    "últimos kilómetros el domingo.",
]

NO_PLANES = [
    "Ahora corres tres kilómetros por semana, así que esta semana vamos a cuatro.",
    "El domingo descansas por completo, que es cuando el cuerpo adapta.",
    "¿Qué tal te fue el martes?",
    "Para armar el plan, cuéntame primero cuántos kilómetros corres a la semana.",
]


@pytest.mark.parametrize("reply", PLANES)
def test_a_week_laid_out_is_worth_reading(reply):
    assert looks_like_a_plan(reply)


@pytest.mark.parametrize("reply", NO_PLANES)
def test_a_reply_that_is_not_a_week_costs_no_request(reply):
    """Every one of these would otherwise spend a request from a 500-a-day
    budget to learn there was nothing to learn."""
    assert not looks_like_a_plan(reply)


def test_one_weekday_is_not_a_week():
    """The reason the gate counts two: a single day appears all over an ordinary
    conversation, and firing on it would put the cost back."""
    assert not looks_like_a_plan("El domingo haces la tirada larga.")


def test_the_gate_reads_english_too():
    """The coach answers in whatever language it was spoken to, so a week given
    in English has to reach the same panel."""
    assert looks_like_a_plan("Run four kilometres on Tuesday and eight on Sunday.")


# --- believing the result ----------------------------------------------------

def test_a_clean_week_survives_intact():
    sessions = clean({"sessions": [
        {"day": 2, "km": 4.0, "note": "suaves"},
        {"day": 7, "km": 14.0, "note": "tirada larga"},
    ]})

    assert sessions == [
        {"day": 2, "km": 4.0, "note": "suaves"},
        {"day": 7, "km": 14.0, "note": "tirada larga"},
    ]


def test_sessions_come_back_in_day_order():
    """The panel prints them as given; sorting here means it never has to."""
    sessions = clean({"sessions": [
        {"day": 7, "km": 14.0, "note": None},
        {"day": 2, "km": 4.0, "note": None},
    ]})

    assert [s["day"] for s in sessions] == [2, 7]


@pytest.mark.parametrize("day", [0, 8, -1, 1.5, True, None, "martes"])
def test_a_day_that_is_not_a_weekday_is_dropped(day):
    assert clean({"sessions": [{"day": day, "km": 5.0, "note": None}]}) == []


@pytest.mark.parametrize("km", [0, -3, 500.0, None, "cuatro", True])
def test_a_distance_that_is_not_one_is_dropped(km):
    """A session with no believable distance is worse than a missing session:
    the runner would go out and run whatever the panel said."""
    assert clean({"sessions": [{"day": 2, "km": km, "note": None}]}) == []


def test_a_day_named_twice_keeps_the_first():
    """Summing them would invent a distance the coach never said."""
    sessions = clean({"sessions": [
        {"day": 4, "km": 12.0, "note": "series"},
        {"day": 4, "km": 99.0, "note": "otra cosa"},
    ]})

    assert sessions == [{"day": 4, "km": 12.0, "note": "series"}]


def test_rubbish_does_not_raise():
    for raw in (None, [], "una semana", {"sessions": "el martes"}, {"sessions": [None, 7]}):
        assert clean(raw) == []


def test_a_long_note_is_cut_rather_than_allowed_to_wrap():
    sessions = clean({"sessions": [
        {"day": 2, "km": 5.0, "note": "series cortas de un minuto a ritmo muy exigente con "
                                      "recuperación completa entre cada repetición"},
    ]})

    assert len(sessions[0]["note"]) <= 40


# --- the total ---------------------------------------------------------------

def test_the_total_is_the_sum_of_what_is_shown():
    """Never extracted. The coach announces a total out loud and the panel shows
    one; taking the model's word for it would let the two differ by a kilometre
    with no way for a runner to tell which was right."""
    sessions = [{"day": 2, "km": 4.0}, {"day": 4, "km": 4.0}, {"day": 7, "km": 14.0}]

    assert week_total(sessions) == 22.0


def test_an_empty_week_totals_nothing():
    assert week_total([]) == 0


# --- persistence -------------------------------------------------------------

def test_a_week_survives_being_stored():
    user = db_service.get_or_create_user(web_session_id="a" * 32)
    sessions = [{"day": 2, "km": 4.0, "note": "suaves"}]

    db_service.save_plan(user["id"], sessions)

    assert db_service.get_plan(user["id"]) == sessions


def test_asking_again_replaces_the_week_rather_than_adding_one():
    user = db_service.get_or_create_user(web_session_id="b" * 32)
    db_service.save_plan(user["id"], [{"day": 2, "km": 4.0, "note": None}])

    db_service.save_plan(user["id"], [{"day": 3, "km": 6.0, "note": None}])

    assert db_service.get_plan(user["id"]) == [{"day": 3, "km": 6.0, "note": None}]


def test_an_empty_extraction_never_wipes_the_panel():
    """The failure a runner would actually notice: asking something unrelated
    and watching the week vanish."""
    user = db_service.get_or_create_user(web_session_id="c" * 32)
    week = [{"day": 2, "km": 4.0, "note": None}]
    db_service.save_plan(user["id"], week)

    db_service.save_plan(user["id"], [])

    assert db_service.get_plan(user["id"]) == week


def test_a_runner_with_no_plan_reads_back_empty():
    user = db_service.get_or_create_user(web_session_id="d" * 32)

    assert db_service.get_plan(user["id"]) == []


# --- the agent ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_coach_reads_its_own_week_back(coach, gemini):
    """The reply is the input, not the runner's message: the week only exists
    once the coach has said it."""
    user = db_service.get_or_create_user(web_session_id="e" * 32)
    gemini.plan_extraction = {"sessions": [
        {"day": 2, "km": 4.0, "note": "suaves"},
        {"day": 7, "km": 14.0, "note": "tirada larga"},
    ]}

    stored = await coach.read_plan(user["id"], PLANES[0])

    assert len(stored) == 2
    assert db_service.get_plan(user["id"]) == stored


@pytest.mark.asyncio
async def test_a_reply_without_a_week_never_reaches_the_model(coach, gemini):
    user = db_service.get_or_create_user(web_session_id="f" * 32)

    assert await coach.read_plan(user["id"], NO_PLANES[0]) == []
    assert gemini.extractions == [], "gastó una petición en una respuesta sin plan"


@pytest.mark.asyncio
async def test_a_failed_reading_leaves_the_previous_week_alone(coach, gemini):
    user = db_service.get_or_create_user(web_session_id="g" * 32)
    week = [{"day": 2, "km": 4.0, "note": None}]
    db_service.save_plan(user["id"], week)
    gemini.extract_fail_with = RuntimeError("boom")

    assert await coach.read_plan(user["id"], PLANES[0]) == []
    assert db_service.get_plan(user["id"]) == week


@pytest.mark.asyncio
async def test_reading_the_week_can_be_switched_off(coach, gemini, monkeypatch):
    """A deployment short on quota keeps the profile and drops the panel, which
    is the cheaper of the two to lose."""
    from src.config import get_settings

    user = db_service.get_or_create_user(web_session_id="h" * 32)
    monkeypatch.setenv("PLAN_EXTRACTION_ENABLED", "false")
    get_settings.cache_clear()

    assert await coach.read_plan(user["id"], PLANES[0]) == []
    assert gemini.extractions == []


@pytest.mark.asyncio
async def test_a_turn_that_lays_out_a_week_says_so(coach, gemini):
    """converse does not wait for the reading, so it tells the client to look
    again instead of holding the reply for a second it does not have."""
    gemini.reply = PLANES[0]

    reply = await coach.converse("dame el plan", web_session_id="i" * 32)

    assert reply.plan_pending is True


@pytest.mark.asyncio
async def test_an_ordinary_turn_does_not_ask_the_client_to_look_again(coach, gemini):
    gemini.reply = NO_PLANES[0]

    reply = await coach.converse("hola", web_session_id="j" * 32)

    assert reply.plan_pending is False


@pytest.mark.asyncio
async def test_a_degraded_reply_is_never_read_for_a_week(coach, gemini):
    """The fallback text is ours, not the coach's. Reading it would spend a
    request on a sentence we wrote."""
    from src.services.gemini_service import GeminiUnavailableError

    gemini.fail_with = GeminiUnavailableError("down")

    reply = await coach.converse("dame el plan", web_session_id="k" * 32)

    assert reply.degraded is True
    assert reply.plan_pending is False


# --- the endpoint ------------------------------------------------------------

def test_the_panel_is_served_the_week_and_its_total(client):
    user = db_service.get_or_create_user(web_session_id="l" * 32)
    db_service.save_plan(user["id"], [
        {"day": 2, "km": 4.0, "note": "suaves"},
        {"day": 7, "km": 14.0, "note": None},
    ])

    body = client.get("/api/profile/" + "l" * 32).json()

    assert [s["day"] for s in body["plan"]] == [2, 7]
    assert body["plan_total_km"] == 18.0


def test_a_runner_with_no_week_gets_an_empty_one_not_an_error(client):
    body = client.get("/api/profile/" + "m" * 32).json()

    assert body["plan"] == []
    assert body["plan_total_km"] == 0


# --- a partial week must not delete the rest ---------------------------------
#
# Found in use, not in a test. The coach was asked about a run already done and
# answered "para lo que queda de semana, mantén el sábado de tres y el domingo
# de nueve tal como lo teníamos previsto". Two weekdays, so the gate fired; the
# reading was those two days; and the stored week went from twenty-two
# kilometres across four days to twelve across two. The runner lost the Tuesday
# and Thursday they were still meant to run, and nothing said so.

SEMANA_COMPLETA = [
    {"day": 2, "km": 5.0, "note": "suaves"},
    {"day": 4, "km": 5.0, "note": None},
    {"day": 6, "km": 3.0, "note": "muy tranquilos"},
    {"day": 7, "km": 9.0, "note": "tirada larga"},
]


def test_a_partial_reading_keeps_the_days_it_did_not_mention():
    from src.coaching.plan import merge

    quedaba = [{"day": 6, "km": 3.0, "note": None}, {"day": 7, "km": 9.0, "note": None}]

    assert [s["day"] for s in merge(SEMANA_COMPLETA, quedaba)] == [2, 4, 6, 7]


def test_naming_a_day_again_corrects_it():
    """"el martes mejor hazlo más suave" is a correction, not a second Tuesday."""
    from src.coaching.plan import merge

    fusion = merge(SEMANA_COMPLETA, [{"day": 2, "km": 3.0, "note": "más suave"}])

    assert fusion[0] == {"day": 2, "km": 3.0, "note": "más suave"}
    assert len(fusion) == 4


def test_a_reading_that_says_nothing_about_scope_is_treated_as_partial():
    """The safe direction. Folding a genuine new week into an old one leaves at
    most a stale day; replacing on a passing mention deletes real training."""
    from src.coaching.plan import replaces_the_week

    assert replaces_the_week({"sessions": []}) is False
    assert replaces_the_week(None) is False
    assert replaces_the_week({"replaces_the_week": "sí"}) is False
    assert replaces_the_week({"replaces_the_week": True}) is True


@pytest.mark.asyncio
async def test_the_reported_case_no_longer_truncates_the_week(coach, gemini):
    """The exact exchange from the screenshot, end to end."""
    user = db_service.get_or_create_user(web_session_id="n" * 32)
    db_service.save_plan(user["id"], SEMANA_COMPLETA)

    gemini.plan_extraction = {
        "sessions": [
            {"day": 6, "km": 3.0, "note": None},
            {"day": 7, "km": 9.0, "note": "tirada larga"},
        ],
        "replaces_the_week": False,
    }
    await coach.read_plan(
        user["id"],
        "Para lo que queda de semana, mantén el sábado de tres kilómetros y el "
        "domingo de nueve tal como lo teníamos previsto.",
    )

    semana = db_service.get_plan(user["id"])
    assert [s["day"] for s in semana] == [2, 4, 6, 7], "volvió a borrar media semana"
    assert week_total(semana) == 22.0


@pytest.mark.asyncio
async def test_a_genuinely_new_week_still_replaces_the_old_one(coach, gemini):
    """The other direction has to keep working: a lighter week must not inherit
    the days it deliberately dropped."""
    user = db_service.get_or_create_user(web_session_id="o" * 32)
    db_service.save_plan(user["id"], SEMANA_COMPLETA)

    gemini.plan_extraction = {
        "sessions": [{"day": 3, "km": 4.0, "note": None}, {"day": 6, "km": 6.0, "note": None}],
        "replaces_the_week": True,
    }
    await coach.read_plan(user["id"], "Esta semana lo hacemos en dos días: miércoles y sábado.")

    assert [s["day"] for s in db_service.get_plan(user["id"])] == [3, 6]
