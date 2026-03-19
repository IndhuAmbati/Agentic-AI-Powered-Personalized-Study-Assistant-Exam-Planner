"""Schedule schemas."""

from __future__ import annotations

from datetime import date, time, datetime
from pydantic import BaseModel, Field


class ScheduleGenerateRequest(BaseModel):
    user_id: str
    start_date: date
    end_date: date
    daily_start_time: time = time(8, 0)
    daily_study_hours: float | None = Field(None, ge=0.5, le=16.0)
    session_duration_mins: int = Field(60, ge=15, le=180)
    break_duration_mins: int = Field(15, ge=5, le=60)
    max_topics_per_day: int | None = Field(None, ge=1, le=12)
    no_ai_mode: bool = False


class ScheduleEntryOut(BaseModel):
    id: str
    user_id: str
    topic_id: str
    subject_name: str
    topic_name: str
    scheduled_date: date
    start_time: time
    end_time: time
    duration_mins: float
    priority_score: float
    is_revision: int
    completed: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ScheduleRescheduleOut(BaseModel):
    status: str
    skipped_entry_id: str
    rescheduled_entry: ScheduleEntryOut


class ScheduleFromSyllabusPdfOut(BaseModel):
    subject_id: str
    subject_name: str
    unit_range: str
    units_detected: list[int]
    topics_created: int
    revision_entries_added: int = 0
    quizzes_generated: int = 0
    coverage_end_date: date | None = None
    schedule_entries: list[ScheduleEntryOut]
