"""The single declarative base.

Every model inherits from this one class. Declaring a base per module gives each
its own MetaData registry, and a ForeignKey cannot then resolve a table it
cannot see: the application fails at import with NoReferencedTableError and the
test suite errors during collection.

That exact failure sank the first attempt at this project, so it is worth a file
of its own rather than a line at the top of one of the model modules.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Timezone-aware default for timestamp columns.

    `datetime.utcnow` is deprecated and returns a naive value, which compares
    incorrectly against aware ones and is ambiguous once the app is deployed
    outside this machine.
    """
    return datetime.now(timezone.utc)
