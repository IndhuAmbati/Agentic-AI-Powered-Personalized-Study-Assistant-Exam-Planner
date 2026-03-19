"""Topic API routes."""

from __future__ import annotations

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models.topic import Topic
from backend.app.schemas.topic import TopicCreate, TopicUpdate, TopicOut
from backend.app.schemas.quiz import (
    QuizGenerateRequest,
    QuizOut,
    TopicReadyQuizOut,
    TopicReadyQuizRequest,
)
from backend.app.services.quiz_engine import create_quiz
from backend.app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.post("", response_model=TopicOut, status_code=201)
async def create_topic(payload: TopicCreate, db: AsyncSession = Depends(get_db)):
    """Add a topic to a subject."""
    topic = Topic(**payload.model_dump())
    db.add(topic)
    await db.flush()
    await db.refresh(topic)
    return topic


@router.get("/subject/{subject_id}", response_model=list[TopicOut])
async def list_topics(subject_id: str, db: AsyncSession = Depends(get_db)):
    """List all topics for a subject."""
    result = await db.execute(
        select(Topic).where(Topic.subject_id == subject_id).order_by(Topic.order_index)
    )
    return list(result.scalars().all())


@router.patch("/{topic_id}", response_model=TopicOut)
async def update_topic(
    topic_id: str, payload: TopicUpdate, db: AsyncSession = Depends(get_db)
):
    """Update a topic."""
    topic = await db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(topic, k, v)
    await db.flush()
    await db.refresh(topic)
    return topic


@router.delete("/{topic_id}", status_code=204)
async def delete_topic(topic_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a topic."""
    topic = await db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")
    await db.delete(topic)
    await db.flush()


@router.post("/{topic_id}/ready-quizzes", response_model=TopicReadyQuizOut)
async def ready_topic_for_quizzes(
    topic_id: str,
    payload: TopicReadyQuizRequest,
    db: AsyncSession = Depends(get_db),
):
    """Mark a topic complete and generate easy/medium/hard quizzes for it."""
    try:
        topic = await db.get(Topic, topic_id)
        if not topic:
            raise HTTPException(404, "Topic not found")
        topic.completed = 1
        topic.completion_pct = 100.0
        topic.last_reviewed = datetime.now(timezone.utc)
        await db.flush()

        difficulties = ["easy", "medium", "hard"]
        quizzes: list[QuizOut] = []

        generated = []
        for difficulty in difficulties:
            quiz = await create_quiz(
                db,
                QuizGenerateRequest(
                    user_id=payload.user_id,
                    topic_id=topic_id,
                    difficulty=difficulty,
                    num_questions=payload.num_questions,
                ),
            )
            generated.append(quiz)
        ordering = {"easy": 0, "medium": 1, "hard": 2}
        quizzes = [QuizOut.from_orm(q) for q in sorted(generated, key=lambda q: ordering.get(q.difficulty, 1))]
        return TopicReadyQuizOut(topic_id=topic_id, quizzes=quizzes)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Ready-quizzes generation hard-failed for topic %s", topic_id)
        return TopicReadyQuizOut(topic_id=topic_id, quizzes=[])
