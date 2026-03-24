"""Mind map API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.database import get_db
from backend.app.models.progress import ProgressRecord
from backend.app.models.subject import Subject
from backend.app.services.mind_map import (
    apply_cached_mind_map_descriptions,
    build_mind_map,
    generate_topic_description,
)

router = APIRouter(prefix="/api/mindmap", tags=["mindmap"])


class TopicDescriptionRequest(BaseModel):
    subject_name: str
    unit_name: str
    topic_label: str


@router.get("/{user_id}")
async def get_mind_map(user_id: str, db: AsyncSession = Depends(get_db)):
    """Return a nested mind map for the user's subjects and topics."""
    result = await db.execute(
        select(Subject)
        .where(Subject.user_id == user_id)
        .options(selectinload(Subject.topics))
        .order_by(Subject.name.asc())
    )
    subjects = result.scalars().unique().all()
    topics = [topic for subject in subjects for topic in subject.topics]
    weak_result = await db.execute(
        select(
            ProgressRecord.topic_id,
            func.count(ProgressRecord.id).label("attempts"),
            func.avg(ProgressRecord.quiz_score).label("average_score"),
        )
        .where(
            ProgressRecord.user_id == user_id,
            ProgressRecord.quiz_score.isnot(None),
        )
        .group_by(ProgressRecord.topic_id)
    )
    weak_topic_stats = {
        row.topic_id: {
            "attempts": int(row.attempts or 0),
            "average_score": float(row.average_score or 0.0),
        }
        for row in weak_result
    }
    payload = build_mind_map(subjects, topics, weak_topic_stats)
    return apply_cached_mind_map_descriptions(payload)


@router.post("/describe-topic")
async def describe_topic(payload: TopicDescriptionRequest):
    try:
        description = await generate_topic_description(
            subject_name=payload.subject_name,
            unit_name=payload.unit_name,
            topic_label=payload.topic_label,
        )
        return {"description": description}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
