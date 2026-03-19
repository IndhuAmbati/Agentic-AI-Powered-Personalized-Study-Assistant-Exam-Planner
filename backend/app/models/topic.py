"""Topic ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Float, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class Topic(Base):
    """A topic within a subject (e.g., 'Calculus — Integrals')."""

    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    subject_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)  # 0–1 scale
    estimated_hours: Mapped[float] = mapped_column(Float, default=2.0)
    completed: Mapped[int] = mapped_column(Integer, default=0)  # 0 or 1
    completion_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 0–100
    time_spent_mins: Mapped[float] = mapped_column(Float, default=0.0)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    subject: Mapped["Subject"] = relationship("Subject", back_populates="topics")  # type: ignore[name-defined]  # noqa: F821
