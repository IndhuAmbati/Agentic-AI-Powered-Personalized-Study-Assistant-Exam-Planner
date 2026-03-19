"""Subject API routes."""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi import UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.models.user import User
from backend.app.schemas.subject import (
    SubjectCreate,
    SubjectUpdate,
    SubjectOut,
    SyllabusImportRequest,
    SyllabusImportOut,
    SyllabusPdfImportOut,
)
from backend.app.services.syllabus_parser import (
    extract_text_from_pdf,
    parse_subjects_and_topics,
)

router = APIRouter(prefix="/api/subjects", tags=["subjects"])


def _normalized_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


@router.post("", response_model=SubjectOut, status_code=201)
async def create_subject(payload: SubjectCreate, db: AsyncSession = Depends(get_db)):
    """Add a new subject for a user."""
    subject = Subject(**payload.model_dump())
    db.add(subject)
    await db.flush()
    await db.refresh(subject)
    return subject


@router.get("/{user_id}", response_model=list[SubjectOut])
async def list_subjects(user_id: str, db: AsyncSession = Depends(get_db)):
    """List all subjects for a user."""
    result = await db.execute(select(Subject).where(Subject.user_id == user_id))
    return list(result.scalars().all())


@router.patch("/{subject_id}", response_model=SubjectOut)
async def update_subject(
    subject_id: str, payload: SubjectUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a subject."""
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404, "Subject not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(subject, k, v)
    await db.flush()
    await db.refresh(subject)
    return subject


@router.delete("/{subject_id}", status_code=204)
async def delete_subject(subject_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a subject and its topics."""
    subject = await db.get(Subject, subject_id)
    if not subject:
        raise HTTPException(404, "Subject not found")
    await db.delete(subject)
    await db.flush()


@router.post("/syllabus/import", response_model=SyllabusImportOut, status_code=201)
async def import_syllabus(
    payload: SyllabusImportRequest, db: AsyncSession = Depends(get_db)
):
    """Create a subject and bulk-create topics from pasted syllabus text."""
    raw_lines = [line.strip() for line in payload.syllabus_text.splitlines()]
    topic_lines = [line.lstrip("-*0123456789. ").strip() for line in raw_lines if line]
    topic_lines = [line for line in topic_lines if line]

    if not topic_lines:
        raise HTTPException(400, "No valid topics found in syllabus text")

    subject = Subject(
        user_id=payload.user_id,
        name=payload.subject_name,
        exam_date=payload.exam_date,
        priority=payload.priority,
        color=payload.color,
    )
    db.add(subject)
    await db.flush()

    for idx, topic_name in enumerate(topic_lines):
        db.add(
            Topic(
                subject_id=subject.id,
                name=topic_name,
                difficulty=payload.default_topic_difficulty,
                estimated_hours=payload.default_topic_hours,
                order_index=idx,
            )
        )

    await db.flush()

    return {
        "subject_id": subject.id,
        "subject_name": subject.name,
        "topics_created": len(topic_lines),
    }


@router.post("/syllabus/import-pdf", response_model=SyllabusPdfImportOut, status_code=201)
async def import_syllabus_pdf(
    user_id: str = Form(...),
    no_of_subjects: int | None = Form(None),
    daily_study_hours: float | None = Form(None),
    exam_dates_json: str | None = Form(None),
    default_topic_hours: float = Form(2.0),
    default_topic_difficulty: float = Form(0.5),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import a single PDF containing multiple subjects and create subjects/topics."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if daily_study_hours is not None:
        user.daily_study_hours = min(max(daily_study_hours, 0.5), 16.0)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(400, "Uploaded PDF is empty")

    try:
        text = extract_text_from_pdf(raw_bytes)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse PDF: {exc}") from exc

    parsed = parse_subjects_and_topics(
        text, expected_subject_count=no_of_subjects
    )
    if not parsed:
        raise HTTPException(
            400,
            "Could not extract subjects/topics from this PDF. Use a clearer PDF or provide syllabus text manually.",
        )

    exam_dates: dict[str, str] = {}
    if exam_dates_json:
        try:
            loaded_exam_dates = json.loads(exam_dates_json)
            if isinstance(loaded_exam_dates, dict):
                exam_dates = {str(k): str(v) for k, v in loaded_exam_dates.items()}
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "exam_dates_json must be valid JSON") from exc

    created_subjects: list[dict] = []
    total_topics = 0

    for idx, parsed_subject in enumerate(parsed):
        subject_name = parsed_subject["name"].strip() or f"Subject {idx + 1}"
        exam_date_raw = (
            exam_dates.get(subject_name)
            or exam_dates.get(_normalized_key(subject_name))
            or exam_dates.get(str(idx + 1))
            or exam_dates.get(f"Subject {idx + 1}")
            or exam_dates.get(f"subject {idx + 1}")
        )
        parsed_exam_date: date | None = None
        if exam_date_raw:
            try:
                parsed_exam_date = date.fromisoformat(exam_date_raw)
            except ValueError:
                parsed_exam_date = None

        subject = Subject(
            user_id=user_id,
            name=subject_name,
            exam_date=parsed_exam_date,
            priority=3.0,
            color="#4A90D9",
        )
        db.add(subject)
        await db.flush()

        topics_created_for_subject = 0
        for topic_index, topic_name in enumerate(parsed_subject["topics"]):
            topic_clean = topic_name.strip()
            if not topic_clean:
                continue
            db.add(
                Topic(
                    subject_id=subject.id,
                    name=topic_clean,
                    difficulty=min(max(default_topic_difficulty, 0.0), 1.0),
                    estimated_hours=max(default_topic_hours, 0.25),
                    order_index=topic_index,
                )
            )
            topics_created_for_subject += 1

        total_topics += topics_created_for_subject
        created_subjects.append(
            {
                "id": subject.id,
                "name": subject.name,
                "topics_created": topics_created_for_subject,
                "exam_date": subject.exam_date.isoformat() if subject.exam_date else None,
            }
        )

    await db.flush()

    return {
        "subjects_created": len(created_subjects),
        "topics_created": total_topics,
        "subjects": created_subjects,
    }
