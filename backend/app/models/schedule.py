"""Schedule entry ORM model."""

from __future__ import annotations

import uuid
from datetime import date, time, datetime

from sqlalchemy import String, Date, Time, Float, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class ScheduleEntry(Base):
    """A single study‐block in the generated schedule."""

    __tablename__ = "schedule_entries"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    subject_name: Mapped[str] = mapped_column(String(200), default="")
    topic_name: Mapped[str] = mapped_column(String(300), default="")
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_mins: Mapped[float] = mapped_column(Float, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_revision: Mapped[int] = mapped_column(default=0)
    completed: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
