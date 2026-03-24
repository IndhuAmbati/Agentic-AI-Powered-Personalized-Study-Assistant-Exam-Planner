"""Progress record ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Float, ForeignKey, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class ProgressRecord(Base):
    """Snapshot of progress for analytics and adaptive planning."""

    __tablename__ = "progress_records"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    completion_pct: Mapped[float] = mapped_column(Float, default=0.0)
    time_spent_mins: Mapped[float] = mapped_column(Float, default=0.0)
    quiz_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
