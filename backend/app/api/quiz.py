"""Quiz API routes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models.quiz import Quiz
from backend.app.schemas.quiz import (
    QuizGenerateRequest,
    QuizOut,
    QuizSubmitRequest,
    QuizResult,
    TopicReadyQuizRequest,
)
from backend.app.services.quiz_engine import create_quiz, evaluate_quiz

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


@router.post("/generate", response_model=QuizOut, status_code=201)
async def generate_quiz(payload: QuizGenerateRequest, db: AsyncSession = Depends(get_db)):
    """Generate a new quiz for a topic."""
    import logging
    from fastapi import HTTPException

    logger = logging.getLogger(__name__)

    try:
        return await create_quiz(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Groq quiz generation failed")
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@router.post("/generate/{topic_id}", response_model=QuizOut, status_code=201)
async def generate_quiz_for_topic(
    topic_id: str,
    payload: TopicReadyQuizRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a quiz for the given topic ID (path param) using Groq.
    Forces the topic id from the path to avoid mismatches from the client payload.
    """
    import logging
    from fastapi import HTTPException

    logger = logging.getLogger(__name__)

    try:
        req = QuizGenerateRequest(
            user_id=payload.user_id,
            topic_id=topic_id,
            difficulty="easy",
            num_questions=payload.num_questions,
        )
        return await create_quiz(db, req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Groq quiz generation failed")
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@router.post("/submit", response_model=QuizResult)
async def submit_quiz(payload: QuizSubmitRequest, db: AsyncSession = Depends(get_db)):
    """Submit answers and get evaluation results."""
    from fastapi import HTTPException

    try:
        return await evaluate_quiz(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/history/{user_id}", response_model=list[QuizOut])
async def quiz_history(user_id: str, db: AsyncSession = Depends(get_db)):
    """Get quiz history for a user."""
    q = (
        select(Quiz)
        .where(Quiz.user_id == user_id)
        .options(selectinload(Quiz.questions))
        .order_by(Quiz.created_at.desc())
    )
    result = await db.execute(q)
    return list(result.scalars().unique().all())


@router.get("/health/groq")
async def groq_health():
    """Lightweight health check for Groq connectivity using the configured API key/proxy."""
    settings = get_settings()
    if not settings.groq_api_key:
        return {"status": "error", "detail": "GROQ_API_KEY missing"}

    transport = (
        httpx.AsyncHTTPTransport(proxy=settings.groq_proxy_url)
        if getattr(settings, "groq_proxy_url", None)
        else None
    )
    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            trust_env=not bool(settings.groq_proxy_url),
            transport=transport,
        ) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        return {
            "status": "ok" if resp.is_success else "error",
            "http_status": resp.status_code,
        }
    except Exception as exc:  # pragma: no cover - network errors
        return {"status": "error", "detail": str(exc)}
