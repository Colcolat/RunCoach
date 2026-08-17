"""A ceiling on how fast one session can spend the shared quota.

Not a general-purpose rate limiter, and deliberately not one. This exists for a
single, concrete reason: the free tier gives 500 text requests a day, a turn can
cost two of them, and there is nothing between an open endpoint and that budget.
A loop of requests empties it in minutes, and the next person to open the demo
meets a coach that cannot answer.

In memory rather than in Redis, because there is one process by design - two
would poll Telegram twice and sweep reminders twice - so a shared store would be
infrastructure bought for a problem this deployment does not have.

Keyed by session rather than by IP. A shared network should not throttle a
whole office, and the session is what actually spends the quota. It is also
trivially resettable by clearing localStorage, which is fine: this protects
against a runaway loop and an idle passer-by, not against someone determined.
The honest name for that limit is in the README.
"""

from __future__ import annotations

import time
from collections import deque


class TurnLimiter:
    """Sliding one-minute window per key."""

    # Prune once the table is bigger than any real minute of traffic could
    # justify. Lazily rather than on a timer: a scheduled job would tie this to
    # the reminder sweep, which does not run at all when Telegram is
    # unconfigured, and leak quietly on exactly the deployments least likely to
    # be watched.
    PRUNE_ABOVE = 1024

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._seen: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        """Whether this key may take another turn, recording it if so."""
        if self._max <= 0:
            return True

        now = time.monotonic() if now is None else now

        # One entry per session id ever seen is the same unbounded growth this
        # class exists to prevent elsewhere.
        if len(self._seen) > self.PRUNE_ABOVE:
            self.forget_idle(now)

        window = self._seen.setdefault(key, deque())

        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= self._max:
            return False

        window.append(now)
        return True

    def forget_idle(self, now: float | None = None) -> int:
        """Drop keys with nothing in the window, so the dict cannot grow forever.

        Without this the limiter is itself a slow memory leak: one entry per
        session id ever seen, which is the same unbounded-growth problem it was
        added to prevent elsewhere.
        """
        now = time.monotonic() if now is None else now
        cutoff = now - 60.0
        stale = [k for k, w in self._seen.items() if not w or w[-1] < cutoff]
        for key in stale:
            del self._seen[key]
        return len(stale)
