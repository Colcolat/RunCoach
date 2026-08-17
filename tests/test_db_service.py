"""Persistence tests.

The first assertion here guards the failure that sank the first attempt at this
project: models declaring one declarative base each, so no ForeignKey could
resolve and the application would not import.
"""

from __future__ import annotations

import pytest

from src.models import Base
from src.services import db_service


@pytest.fixture(autouse=True)
def schema():
    db_service.create_schema()


def test_every_table_shares_one_metadata_registry():
    assert set(Base.metadata.tables) == {
        "users",
        "conversations",
        "messages",
        "reminders",
        "training_plans",
    }


# --- identity ----------------------------------------------------------------


def test_the_same_session_id_resolves_to_the_same_runner():
    first = db_service.get_or_create_user(web_session_id="s1", username="Juan")
    second = db_service.get_or_create_user(web_session_id="s1")

    assert first["id"] == second["id"]
    assert second["username"] == "Juan"


def test_different_channels_are_different_runners():
    web = db_service.get_or_create_user(web_session_id="s1")
    telegram = db_service.get_or_create_user(telegram_id=99)

    assert web["id"] != telegram["id"]


def test_a_runner_needs_some_identity():
    with pytest.raises(ValueError):
        db_service.get_or_create_user()


def test_an_existing_name_is_not_overwritten():
    db_service.get_or_create_user(web_session_id="s1", username="Juan")
    again = db_service.get_or_create_user(web_session_id="s1", username="Otro")

    assert again["username"] == "Juan"


# --- conversations -----------------------------------------------------------


def test_a_conversation_id_survives_its_session_closing():
    """Returning the ORM object instead would raise DetachedInstanceError here."""
    user = db_service.get_or_create_user(web_session_id="s1")

    conversation_id = db_service.get_or_create_conversation(user["id"])

    assert isinstance(conversation_id, int)
    assert db_service.get_or_create_conversation(user["id"]) == conversation_id


# --- history -----------------------------------------------------------------


def test_history_comes_back_oldest_first():
    conversation_id = _conversation()
    db_service.save_message(conversation_id, "user", "primera")
    db_service.save_message(conversation_id, "assistant", "segunda")
    db_service.save_message(conversation_id, "user", "tercera")

    history = db_service.get_history(conversation_id)

    assert [m["content"] for m in history] == ["primera", "segunda", "tercera"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]


def test_the_limit_keeps_the_most_recent_turns():
    """A long conversation should forget its beginning, not its present."""
    conversation_id = _conversation()
    for index in range(10):
        db_service.save_message(conversation_id, "user", f"mensaje-{index}")

    history = db_service.get_history(conversation_id, limit=3)

    assert [m["content"] for m in history] == ["mensaje-7", "mensaje-8", "mensaje-9"]


def test_voice_and_text_share_one_history():
    """A conversation started out loud must continue in writing."""
    conversation_id = _conversation()
    db_service.save_message(conversation_id, "user", "hablado", channel="voice")
    db_service.save_message(conversation_id, "user", "escrito", channel="web")

    history = db_service.get_history(conversation_id)

    assert [m["content"] for m in history] == ["hablado", "escrito"]
    assert [m["channel"] for m in history] == ["voice", "web"]


def test_an_unknown_role_is_rejected():
    with pytest.raises(ValueError):
        db_service.save_message(_conversation(), "system", "no permitido")


# --- profile -----------------------------------------------------------------


def test_the_profile_updates():
    user = db_service.get_or_create_user(web_session_id="s1")

    updated = db_service.update_profile(user["id"], goal="21K", weekly_km=42)

    assert updated["goal"] == "21K"
    assert updated["weekly_km"] == 42


def test_unknown_profile_fields_are_ignored():
    """F4 fills this from model output; a hallucinated key must not be written."""
    user = db_service.get_or_create_user(web_session_id="s1")

    updated = db_service.update_profile(user["id"], goal="5K", inventado="x")

    assert updated["goal"] == "5K"
    assert "inventado" not in updated


def test_updating_a_missing_runner_raises():
    with pytest.raises(ValueError):
        db_service.update_profile(9999, goal="5K")


def _conversation() -> int:
    user = db_service.get_or_create_user(web_session_id="s1")
    return db_service.get_or_create_conversation(user["id"])
