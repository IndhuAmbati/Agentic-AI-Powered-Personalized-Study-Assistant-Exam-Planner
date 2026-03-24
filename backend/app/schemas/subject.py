"""Subject schemas."""

from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field


class SubjectCreate(BaseModel):
    user_id: str
    name: str = Field(..., min_length=1, max_length=200)
    exam_date: date | None = None
    priority: float = Field(1.0, ge=0, le=5)
    color: str = Field("#4A90D9")


class SubjectUpdate(BaseModel):
    name: str | None = None
    exam_date: date | None = None
    priority: float | None = None
    color: str | None = None


class SubjectOut(BaseModel):
    id: str
    user_id: str
    name: str
    exam_date: date | None
    priority: float
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SyllabusImportRequest(BaseModel):
    user_id: str
    subject_name: str = Field(..., min_length=1, max_length=200)
    exam_date: date | None = None
    priority: float = Field(1.0, ge=0, le=5)
    color: str = Field("#4A90D9")
    default_topic_hours: float = Field(2.0, ge=0.25, le=100)
    default_topic_difficulty: float = Field(0.5, ge=0, le=1)
    syllabus_text: str = Field(..., min_length=1)


class SyllabusImportOut(BaseModel):
    subject_id: str
    subject_name: str
    topics_created: int


class SyllabusPdfImportOut(BaseModel):
    subjects_created: int
    topics_created: int
    subjects: list[dict]
