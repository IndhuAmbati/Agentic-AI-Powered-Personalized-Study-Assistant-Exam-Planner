"""User ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class User(Base):
    """Represents a student / user of the system."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    daily_study_hours: Mapped[float] = mapped_column(Float, default=4.0)
    learning_preference: Mapped[str] = mapped_column(
        String(50), default="balanced"
    )  # visual, reading, practice, balanced
    difficulty_level: Mapped[str] = mapped_column(
        String(20), default="medium"
    )  # easy, medium, hard
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    subjects: Mapped[list["Subject"]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        "Subject", back_populates="user", cascade="all, delete-orphan"
    )
