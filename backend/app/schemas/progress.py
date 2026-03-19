"""Progress schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ProgressUpdate(BaseModel):
    user_id: str
    topic_id: str
    completion_pct: float = Field(0.0, ge=0, le=100)
    time_spent_mins: float = Field(0.0, ge=0)
    quiz_score: float | None = None
    notes: str | None = None


class ProgressOut(BaseModel):
    id: str
    user_id: str
    topic_id: str
    completion_pct: float
    time_spent_mins: float
    quiz_score: float | None
    notes: str | None
    recorded_at: datetime

    model_config = {"from_attributes": True}


class ProgressDashboard(BaseModel):
    user_id: str
    total_topics: int
    completed_topics: int
    overall_completion_pct: float
    total_time_spent_mins: float
    average_quiz_score: float | None
    total_schedule_entries: int = 0
    completed_schedule_entries: int = 0
    total_quizzes_taken: int = 0
    quizzes_passed: int = 0
    weak_topics: list[str]
    upcoming_topics: list[str]
    mastery_pending: list[dict] = []
