"""The transport layer: starting, stopping, routing and delivering.

This module sat at 44 percent because the polling lifecycle had no tests at all,
which is precisely the part that runs unattended on the server. The handlers are
closures created inside start(), so they are reached by driving a fake
Application rather than by importing them.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.telegram_service import (
    LINKED_GREETING,
    UNLINKED_GREETING,
    TelegramService,
    TelegramUnavailableError,
)


class FakeMessage:
    def __init__(self, text: str | None = "hola") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    """Only what the handlers actually touch."""

    def __init__(self, chat_id: int = 42, name: str | None = "Juan",
                 message: FakeMessage | None = None, edited: bool = False) -> None:
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.effective_user = type("User", (), {"first_name": name})() if name else None
        # An edited message arrives with `message` empty and `edited_message` set.
        self.message = None if edited else message
        self.effective_message = message


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail_with: Exception | None = None

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((chat_id, text))


class FakeUpdater:
    def __init__(self) -> None:
        self.polling = False
        self.dropped_pending = None

    async def start_polling(self, drop_pending_updates: bool = False) -> None:
        self.polling = True
        self.dropped_pending = drop_pending_updates

    async def stop(self) -> None:
        self.polling = False


class FakeApplication:
    def __init__(self) -> None:
        self.bot = FakeBot()
        self.updater = FakeUpdater()
        self.handlers: list = []
        self.started = False
        self.shut_down = False

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None: ...

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def shutdown(self) -> None:
        self.shut_down = True


@pytest.fixture
def service(monkeypatch):
    """A service whose Application is ours, so start() can be driven."""
    app = FakeApplication()

    class Builder:
        def token(self, _): return self
        def build(self): return app

    monkeypatch.setattr(
        "src.services.telegram_service.Application",
        type("A", (), {"builder": staticmethod(lambda: Builder())}),
    )
    svc = TelegramService(settings=Settings(telegram_bot_token="t", telegram_bot_username="Bot"))
    return svc, app


async def _noop_link(payload, chat_id, name):
    return LINKED_GREETING


async def _noop_message(chat_id, text, name):
    return f"eco: {text}"


# --- the lifecycle -----------------------------------------------------------

@pytest.mark.asyncio
async def test_starting_registers_handlers_and_begins_polling(service):
    svc, app = service

    await svc.start(on_link=_noop_link, on_message=_noop_message)

    assert app.started
    assert app.updater.polling
    assert len(app.handlers) == 2


@pytest.mark.asyncio
async def test_pending_updates_are_dropped_on_start(service):
    """Otherwise a restart replays every message sent while the bot was down,
    answering questions the runner has long since given up on."""
    svc, app = service

    await svc.start(on_link=_noop_link, on_message=_noop_message)

    assert app.updater.dropped_pending is True


@pytest.mark.asyncio
async def test_without_a_token_nothing_starts():
    svc = TelegramService(settings=Settings())

    await svc.start(on_link=_noop_link, on_message=_noop_message)

    assert svc.enabled is False


@pytest.mark.asyncio
async def test_stopping_shuts_everything_down(service):
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    await svc.stop()

    assert not app.updater.polling
    assert not app.started
    assert app.shut_down


@pytest.mark.asyncio
async def test_stopping_something_that_never_started_is_harmless():
    await TelegramService(settings=Settings()).stop()


@pytest.mark.asyncio
async def test_a_failure_during_shutdown_does_not_propagate(service):
    """Shutdown runs while the process is already going down; raising here would
    replace the real cause of the exit with a cleanup error."""
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    async def explode():
        raise RuntimeError("boom")

    app.updater.stop = explode

    await svc.stop()  # must not raise


# --- routing -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_hands_the_deep_link_payload_to_the_linker(service):
    svc, app = service
    visto = {}

    async def on_link(payload, chat_id, name):
        visto.update(payload=payload, chat_id=chat_id, name=name)
        return LINKED_GREETING

    await svc.start(on_link=on_link, on_message=_noop_message)
    handler = app.handlers[0]

    message = FakeMessage()
    context = type("Ctx", (), {"args": ["a" * 32]})()
    await handler.callback(FakeUpdate(message=message), context)

    assert visto == {"payload": "a" * 32, "chat_id": 42, "name": "Juan"}
    assert message.replies == [LINKED_GREETING]


@pytest.mark.asyncio
async def test_start_with_no_payload_still_answers(service):
    svc, app = service
    await svc.start(on_link=lambda *a: _answer(UNLINKED_GREETING), on_message=_noop_message)

    message = FakeMessage()
    context = type("Ctx", (), {"args": []})()
    await app.handlers[0].callback(FakeUpdate(message=message), context)

    assert message.replies == [UNLINKED_GREETING]


@pytest.mark.asyncio
async def test_a_text_message_reaches_the_coach(service):
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    message = FakeMessage("corro 20 km")
    await app.handlers[1].callback(FakeUpdate(message=message), None)

    assert message.replies == ["eco: corro 20 km"]


@pytest.mark.asyncio
async def test_an_edited_message_does_not_crash_the_handler(service):
    """update.message is None for an edit, and every attribute on it raises.
    The handler reads effective_message for exactly this reason."""
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    message = FakeMessage("lo corregi")
    await app.handlers[1].callback(FakeUpdate(message=message, edited=True), None)

    assert message.replies == ["eco: lo corregi"]


@pytest.mark.asyncio
async def test_an_update_carrying_no_message_at_all_is_ignored(service):
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    await app.handlers[1].callback(FakeUpdate(message=None), None)  # must not raise


# --- delivery ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_sending_before_starting_is_an_error_the_sweep_can_catch(service):
    svc, _ = service

    with pytest.raises(TelegramUnavailableError):
        await svc.send(42, "hola")


@pytest.mark.asyncio
async def test_a_message_is_delivered(service):
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)

    await svc.send(42, "toca entrenar")

    assert app.bot.sent == [(42, "toca entrenar")]


@pytest.mark.asyncio
async def test_a_blocked_bot_becomes_one_error_type_for_the_caller(service):
    """The sweep decides what to do about a failed delivery, and it should not
    have to know what python-telegram-bot raises."""
    svc, app = service
    await svc.start(on_link=_noop_link, on_message=_noop_message)
    app.bot.fail_with = ValueError("Forbidden: bot was blocked by the user")

    with pytest.raises(TelegramUnavailableError):
        await svc.send(42, "hola")


async def _answer(text: str) -> str:
    return text
