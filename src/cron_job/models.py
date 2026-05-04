"""Database models for the Cron Job MCP server"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.dialects.postgresql import UUID


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


class CronJob(Base):
    """
    CronJob model for persisting scheduled bot runs.

    This model maps to the 'app_cronjob' table in the database.
    It stores the cron expression and calculates the next execution time.
    """

    __tablename__ = "app_cronjob"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    bot_id = Column(
        UUID(as_uuid=True), ForeignKey("app_bot.id"), nullable=False, index=True
    )
    name = Column(String(100), nullable=False)
    cron_expression = Column(String(100), nullable=False)
    next_run_at = Column(DateTime(timezone=True), nullable=False)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
