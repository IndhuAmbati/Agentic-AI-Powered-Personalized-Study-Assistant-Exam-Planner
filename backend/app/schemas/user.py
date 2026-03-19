"""User schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., max_length=255)
    daily_study_hours: float = Field(4.0, ge=0.5, le=16.0)
    learning_preference: str = Field("balanced")
    difficulty_level: str = Field("medium")


class UserUpdate(BaseModel):
    name: str | None = None
    daily_study_hours: float | None = None
    learning_preference: str | None = None
    difficulty_level: str | None = None


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    daily_study_hours: float
    learning_preference: str
    difficulty_level: str
    created_at: datetime

    model_config = {"from_attributes": True}
