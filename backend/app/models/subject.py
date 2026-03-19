"""Subject ORM model."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import String, Date, Float, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class Subject(Base):
    """A subject the student is studying (e.g., Mathematics, Physics)."""

    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    exam_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[float] = mapped_column(Float, default=1.0)  # 0‒5 scale
    color: Mapped[str] = mapped_column(String(7), default="#4A90D9")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="subjects")  # type: ignore[name-defined]  # noqa: F821
    topics: Mapped[list["Topic"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Topic", back_populates="subject", cascade="all, delete-orphan"
    )
