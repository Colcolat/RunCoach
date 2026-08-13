"""Conversations and the turns inside them."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, utcnow

ROLES = ("user", "assistant")
CHANNELS = ("web", "voice", "telegram")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)

    # Where the turn came from. History is unified across interfaces, so a
    # conversation started by voice continues by text without losing context,
    # but it stays auditable per channel.
    channel: Mapped[str] = mapped_column(String(16), default="web")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    # History is always read as "the last N turns of this conversation", so the
    # index matches the query rather than the column order.
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)


from src.models.user import User  # noqa: E402  (resolves the annotation)
