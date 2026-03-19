"""Topic schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class TopicCreate(BaseModel):
    subject_id: str
    name: str = Field(..., min_length=1, max_length=300)
    difficulty: float = Field(0.5, ge=0, le=1)
    estimated_hours: float = Field(2.0, ge=0.25, le=100)
    order_index: int = 0


class TopicUpdate(BaseModel):
    name: str | None = None
    difficulty: float | None = None
    estimated_hours: float | None = None
    completed: int | None = None
    completion_pct: float | None = None
    time_spent_mins: float | None = None


class TopicOut(BaseModel):
    id: str
    subject_id: str
    name: str
    difficulty: float
    estimated_hours: float
    completed: int
    completion_pct: float
    time_spent_mins: float
    revision_count: int
    last_reviewed: datetime | None
    order_index: int
    created_at: datetime

    model_config = {"from_attributes": True}
