"""Memory as the API actually exposes it.

The regression these exist for: the chat route called handle_message without
history, so every request stood alone. The coach re-asked questions it had just
been answered, and a bare "5" produced a greeting from scratch. The earlier fix
was verified by passing history by hand, which is not what the route did.

So these drive the endpoint, not the agent.
"""

from __future__ import annotations

from src.services import db_service


def test_the_coach_receives_the_previous_turns(client, gemini):
    client.post("/api/chat", json={"message": "Quiero preparar un 5K", "session_id": "s1"})
    client.post("/api/chat", json={"message": "Corro 3 km por semana", "session_id": "s1"})

    history = gemini.calls[-1]["history"]

    assert [m["content"] for m in history] == [
        "Quiero preparar un 5K",
        gemini.reply,
    ]


def test_a_short_reply_still_arrives_with_its_context(client, gemini):
    """The bug in the wild: "5" alone made the coach greet as if new."""
    client.post("/api/chat", json={"message": "¿Cuántos días entreno?", "session_id": "s1"})
    client.post("/api/chat", json={"message": "5", "session_id": "s1"})

    assert gemini.calls[-1]["history"] != []


def test_both_sides_of_the_exchange_are_persisted(client, gemini):
    response = client.post(
        "/api/chat", json={"message": "Quiero un 10K", "session_id": "s1"}
    )
    conversation_id = response.json()["conversation_id"]

    history = db_service.get_history(conversation_id)

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "Quiero un 10K"
    assert history[1]["content"] == gemini.reply


def test_separate_sessions_do_not_share_memory(client, gemini):
    client.post("/api/chat", json={"message": "soy la sesión A", "session_id": "sA"})
    client.post("/api/chat", json={"message": "soy la sesión B", "session_id": "sB"})

    assert gemini.calls[-1]["history"] == []


def test_a_request_without_a_session_id_gets_one(client):
    body = client.post("/api/chat", json={"message": "hola"}).json()

    assert len(body["session_id"]) == 32


def test_the_session_id_is_echoed_so_the_browser_can_keep_it(client):
    body = client.post("/api/chat", json={"message": "hola", "session_id": "s1"}).json()

    assert body["session_id"] == "s1"


def test_history_can_be_replayed_after_a_reload(client, gemini):
    client.post("/api/chat", json={"message": "Quiero un 21K", "session_id": "s1"})

    messages = client.get("/api/history/s1").json()["messages"]

    assert [m["content"] for m in messages] == ["Quiero un 21K", gemini.reply]


def test_replaying_an_unknown_session_is_empty_not_an_error(client):
    response = client.get("/api/history/desconocida")

    assert response.status_code == 200
    assert response.json()["messages"] == []


def test_the_stored_profile_reaches_the_prompt(client, gemini):
    client.post("/api/chat", json={"message": "hola", "session_id": "s1"})
    user = db_service.get_or_create_user(web_session_id="s1")
    db_service.update_profile(user["id"], goal="21K")

    client.post("/api/chat", json={"message": "¿y ahora?", "session_id": "s1"})

    assert "21K" in gemini.calls[-1]["system_prompt"]


def test_a_spoken_turn_lands_in_the_same_history_as_text(client, coach, gemini):
    """Voice and text are one conversation, which is what makes the coach coherent."""
    client.post("/api/chat", json={"message": "escrito", "session_id": "s1"})

    coach.remember_voice_turn("s1", "user", "hablado")

    messages = client.get("/api/history/s1").json()["messages"]
    assert [m["content"] for m in messages] == ["escrito", gemini.reply, "hablado"]
