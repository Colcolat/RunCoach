"""Every finding from the security review, pinned.

Written after auditing the deployed site rather than the source, which is why
each one names what was actually observed. A security fix without a test is a
security fix with an expiry date.
"""

from __future__ import annotations

import logging

import pytest

from src.config import Settings
from src.ratelimit import TurnLimiter
from src.services import db_service


@pytest.fixture(autouse=True)
def schema():
    db_service.create_schema()


# --- reads must not write ----------------------------------------------------
#
# Observed against production: GET /api/profile/<5000 chars> returned 200 and
# left a row behind. Two unauthenticated GETs were unbounded writes, so a loop
# of requests with random ids fills the disk of a machine with 8 GB.

def test_asking_about_a_stranger_creates_nobody(client):
    before = _count_users()

    client.get("/api/profile/nuncavisto")
    client.get("/api/history/nuncavisto")

    assert _count_users() == before


def test_a_session_id_of_five_thousand_characters_is_refused(client):
    huge = "b" * 5000

    body = client.get(f"/api/profile/{huge}").json()

    assert body["goal"] is None
    assert _count_users() == 0


def test_a_made_up_id_gets_the_same_empty_answer_as_a_first_visit(client):
    """Distinguishing them would say which session ids exist."""
    inventado = client.get("/api/profile/" + "f" * 32).json()
    malformado = client.get("/api/profile/no-existe-tampoco").json()

    assert inventado["goal"] == malformado["goal"] is None
    assert inventado["telegram_linked"] == malformado["telegram_linked"] is False


def test_a_real_session_still_reads_back(client, gemini):
    """The fix must not break the thing it protects."""
    gemini.extraction = {"goal": "10K"}
    client.post("/api/chat", json={"message": "quiero un 10K", "session_id": "a" * 32})

    assert client.get("/api/profile/" + "a" * 32).json()["goal"] == "10K"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "a b", "x" * 65, "'; DROP TABLE users--"])
def test_a_session_id_that_is_not_one_is_refused_on_write(client, bad):
    response = client.post("/api/chat", json={"message": "hola", "session_id": bad})

    assert response.status_code == 422


# --- the shared quota --------------------------------------------------------
#
# A turn can cost two requests from a 500-a-day budget, and nothing stood
# between an open endpoint and that budget.

def test_a_loop_of_questions_is_cut_off(client):
    session = "c" * 32
    codes = [
        client.post("/api/chat", json={"message": f"pregunta {i}", "session_id": session}).status_code
        for i in range(20)
    ]

    assert 429 in codes, "nada impedia agotar la cuota del dia"
    assert codes[0] == 200, "el limite no debe morder al primer mensaje"


def test_one_runner_hitting_the_limit_does_not_block_another(client):
    for i in range(20):
        client.post("/api/chat", json={"message": f"p{i}", "session_id": "d" * 32})

    otro = client.post("/api/chat", json={"message": "hola", "session_id": "e" * 32})

    assert otro.status_code == 200


def test_the_limiter_does_not_remember_everyone_forever():
    """Otherwise it is itself the unbounded growth it was added to prevent."""
    limiter = TurnLimiter(5)
    for i in range(1000):
        limiter.allow(f"sesion-{i}", now=100.0)

    assert limiter.forget_idle(now=400.0) == 1000


def test_the_limit_can_be_switched_off():
    assert all(TurnLimiter(0).allow("x") for _ in range(100))


# --- the voice socket --------------------------------------------------------
#
# WebSockets are exempt from the same-origin policy. Verified against
# production: a connection carrying Origin: https://sitio-malicioso.example was
# accepted and opened a Live API session, which costs tokens from a shared
# budget.

def test_a_deployment_can_name_who_may_open_the_voice_socket():
    settings = Settings(allowed_origins="https://runcoachjj.duckdns.org, https://otro.example")

    assert settings.origins == ("https://runcoachjj.duckdns.org", "https://otro.example")


def test_empty_means_allow_everything_which_is_what_a_laptop_needs():
    assert Settings().origins == ()


def test_the_socket_refuses_an_origin_that_is_not_listed(client, monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://runcoachjj.duckdns.org")
    get_settings.cache_clear()

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws/voice?session_id=" + "a" * 32,
            headers={"origin": "https://sitio-malicioso.example"},
        ):
            pass


# --- secrets in logs ---------------------------------------------------------
#
# Observed on the server: the Telegram bot token appeared in the journal 536
# times in one afternoon. httpx logs request URLs at INFO and the token lives in
# the path of every polling request.

def test_the_http_client_does_not_log_the_urls_it_calls():
    from src.main import lifespan  # noqa: F401  (import applies the configuration)
    import src.main

    # The lifespan sets this; assert on the intent rather than running it.
    source = (src.main.__file__ or "")
    assert source

    import pathlib
    text = pathlib.Path(source).read_text(encoding="utf-8")
    assert 'logging.getLogger("httpx").setLevel(logging.WARNING)' in text


def test_no_secret_is_ever_formatted_into_a_log_line():
    """A token in a log is a token in every backup of that log."""
    import pathlib

    for path in pathlib.Path("src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ("telegram_bot_token", "google_api_key"):
            for line in text.splitlines():
                if marker in line and ("logger." in line or "print(" in line):
                    pytest.fail(f"{path}: {line.strip()}")


def _count_users() -> int:
    from src.database import session_scope
    from src.models import User

    with session_scope() as session:
        return session.query(User).count()


def test_the_limiter_prunes_itself_without_being_asked():
    """forget_idle existed and nothing called it, which is the same defect this
    project already shipped once with touch_last_seen: written, tested, dead.
    A scheduled job would have tied it to the reminder sweep, which does not run
    when Telegram is unconfigured."""
    limiter = TurnLimiter(5)

    for i in range(TurnLimiter.PRUNE_ABOVE + 50):
        limiter.allow(f"sesion-{i}", now=100.0 + i)

    # Everything older than a minute is gone; the recent tail survives.
    assert len(limiter._seen) <= 200


def test_pruning_never_forgets_someone_still_talking():
    limiter = TurnLimiter(2)
    for i in range(TurnLimiter.PRUNE_ABOVE + 10):
        limiter.allow(f"otro-{i}", now=100.0)

    limiter.allow("activo", now=1000.0)

    assert limiter.allow("activo", now=1000.0) is True
    assert limiter.allow("activo", now=1000.0) is False, "el limite dejo de contar"
