from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "sqlite:///./runcoach.db"

    google_api_key: str = ""
    # Model ids live in configuration, not in code: gemini-2.5-flash was retired
    # mid-project, and a retirement should be an env change rather than a deploy.
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_live_model: str = "gemini-3.1-flash-live-preview"

    # Reasoning models charge their thinking against this budget, so it has to
    # leave room for both. The lite models do not think, but the headroom costs
    # nothing and protects the config against a model swap.
    gemini_max_output_tokens: int = 1200
    gemini_temperature: float = 0.7

    # Profile extraction is a second request per qualifying turn. Measured at
    # about 300 tokens, so 400 leaves room without inviting a runaway. Turning
    # it off costs personalisation and buys back half the daily request budget,
    # which is the trade worth having available on the free tier.
    profile_extraction_enabled: bool = True
    # The second extraction, reading the week out of the coach's own reply.
    # Separate switch so a deployment short on quota can keep the profile and
    # drop the panel, which is the cheaper thing to lose.
    plan_extraction_enabled: bool = True
    gemini_extraction_max_output_tokens: int = 400

    # Deliberately a different model from the coaching one. Rate limits are per
    # model, so extraction on its own id draws from its own 15/minute bucket
    # instead of competing with the replies the runner is waiting for. Measured
    # on the free tier: the coaching model peaked at 22 requests a minute
    # against a ceiling of 15 while this one sat at 1. Same family, same price,
    # and reading four fields out of a sentence does not need the better model.
    gemini_extraction_model: str = "gemini-3.1-flash-lite"

    # How many past turns are replayed to the model. Bounded because the free
    # tier caps tokens per minute, and because old turns stop being useful.
    history_limit: int = 20

    # Voice budget per session. Audio spends far more tokens per minute than
    # text, and the free tier caps tokens per minute rather than requests, so
    # one long conversation could leave the next visitor with a mute demo.
    # Measured basis: a turn costs roughly 300-500 tokens against a 65K/min
    # ceiling, so this is generous for one runner and still bounded.
    voice_max_seconds: float = 300.0

    # No wait on the Live socket is unbounded: with input the model does not
    # recognise as speech, no turn ever completes and a naive loop hangs.
    voice_idle_timeout: float = 60.0

    # Telegram, from @BotFather. The username has no @ and is only used to build
    # the deep link the web client offers, so a missing one hides that button
    # rather than producing a broken t.me address.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""

    # Reminders are the one thing a closed tab cannot do, and the only part of
    # the system that acts without being asked. All of it is switchable.
    reminders_enabled: bool = True

    # "Seven in the morning" is a wall clock, and a wall clock needs a place.
    # One zone for everyone is a real limitation, stated in the README: the
    # browser knows its own, and asking for it is a later fragment.
    reminder_timezone: str = "America/Mexico_City"

    # How often the sweep asks what is due. A minute is fine: the query is one
    # indexed read, and a coarser tick would make the grace window do more work
    # than it should.
    reminder_sweep_seconds: int = 60

    # Silence long enough to be worth noticing, and how long to wait before
    # noticing it again. Without the cooldown the same silence nudges on every
    # sweep, which is how a helpful bot gets blocked.
    inactivity_after_days: int = 3
    inactivity_cooldown_days: int = 7

    # Origins allowed to open the voice socket. WebSockets are not covered by
    # the same-origin policy, so without this any web page anywhere can open a
    # session against this server and spend the Live API budget. Empty means
    # allow everything, which is what a laptop needs and what a deployment
    # should never have.
    allowed_origins: str = ""

    # A ceiling on turns per session per minute. The free tier gives 500 text
    # requests a day and a turn can cost two, so a loop of requests empties the
    # quota in minutes and leaves the next visitor with a coach that cannot
    # answer. This protects the demo from being trivially switched off.
    max_turns_per_minute: int = 12

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.allowed_origins.split(",") if o.strip())

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.google_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process.

    Tests clear the cache after changing the environment; see
    tests/conftest.py.
    """
    return Settings()
