"""Adaptive AI Agent API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.services.adaptive_agent import AdaptiveAgent

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/adapt")
async def adapt_plan(user_id: str, db: AsyncSession = Depends(get_db)):
    """Trigger the adaptive agent for a user (Observe → Plan → Act → Reflect)."""
    agent = AdaptiveAgent(db, user_id)
    reflection = await agent.run()
    return {
        "user_id": user_id,
        "schedule_entries_created": reflection.schedule_entries_created,
        "topics_boosted": reflection.topics_boosted,
        "insights": reflection.insights,
    }


@router.get("/insights/{user_id}")
async def get_insights(user_id: str, db: AsyncSession = Depends(get_db)):
    """Run the observe + plan phases to get insights without acting."""
    agent = AdaptiveAgent(db, user_id)
    obs = await agent.observe()
    plan = await agent.plan_actions()
    return {
        "user_id": user_id,
        "observation": {
            "total_topics": obs.total_topics,
            "completed_topics": obs.completed_topics,
            "overall_progress_pct": round(obs.overall_progress_pct, 1),
            "weak_topics": obs.weak_topics,
            "overdue_sessions": len(obs.overdue_topics),
            "avg_quiz_score": obs.avg_quiz_score,
            "days_until_next_exam": obs.days_until_next_exam,
        },
        "plan": {
            "reschedule": plan.reschedule,
            "topics_to_boost": plan.boost_topics,
            "suggested_quizzes": plan.suggested_quizzes,
            "messages": plan.messages,
        },
    }
