"""Schemas for syllabus preview/confirm workflow."""

from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, Field

from backend.app.schemas.schedule import ScheduleEntryOut


class SyllabusPreviewSubject(BaseModel):
    name: str
    topics: list[str]


class SyllabusPreviewOut(BaseModel):
    subjects_detected: int
    topics_detected: int
    subjects: list[SyllabusPreviewSubject]


class SyllabusConfirmRequest(BaseModel):
    user_id: str
    start_date: date
    end_date: date
    daily_start_time: time = time(8, 0)
    daily_study_hours: float | None = Field(None, ge=0.5, le=16.0)
    session_duration_mins: int = Field(60, ge=15, le=180)
    break_duration_mins: int = Field(15, ge=5, le=60)
    max_topics_per_day: int | None = Field(5, ge=1, le=12)
    no_ai_mode: bool = False
    clear_existing: bool = True
    default_topic_hours: float = Field(2.0, ge=0.25, le=100)
    default_topic_difficulty: float = Field(0.5, ge=0, le=1)
    subjects: list[SyllabusPreviewSubject]


class SyllabusConfirmOut(BaseModel):
    subjects_created: int
    topics_created: int
    schedule_entries: list[ScheduleEntryOut]
