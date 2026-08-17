"""Importing this package registers every model on the single Base.

The imports are what populate Base.metadata, so create_all() sees every
table and every ForeignKey resolves. A test asserts the registry contents for
exactly that reason.
"""

from src.models.base import Base, utcnow
from src.models.conversation import CHANNELS, ROLES, Conversation, Message
from src.models.plan import TrainingPlan
from src.models.reminder import REMINDER_KINDS, Reminder
from src.models.user import User

__all__ = [
    "Base",
    "utcnow",
    "User",
    "Conversation",
    "Message",
    "Reminder",
    "TrainingPlan",
    "ROLES",
    "CHANNELS",
    "REMINDER_KINDS",
]
