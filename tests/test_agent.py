"""Tests for the adaptive agent logic."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import User
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.services.adaptive_agent import AdaptiveAgent


async def _seed(db: AsyncSession) -> str:
    """Insert a user with subjects, topics, and return user_id."""
    user = User(name="Test Student", email="test@test.com", daily_study_hours=4.0)
    db.add(user)
    await db.flush()

    subj = Subject(
        user_id=user.id,
        name="Mathematics",
        exam_date=date(2026, 2, 20),
        priority=4.0,
    )
    db.add(subj)
    await db.flush()

    topics = [
        Topic(subject_id=subj.id, name="Algebra", difficulty=0.6, completion_pct=20, estimated_hours=5),
        Topic(subject_id=subj.id, name="Calculus", difficulty=0.8, completion_pct=10, estimated_hours=8),
        Topic(subject_id=subj.id, name="Statistics", difficulty=0.4, completion_pct=80, estimated_hours=3),
    ]
    db.add_all(topics)
    await db.flush()
    return user.id


class TestAdaptiveAgent:
    @pytest.mark.asyncio
    async def test_observe_detects_weak_topics(self, db_session: AsyncSession):
        user_id = await _seed(db_session)
        agent = AdaptiveAgent(db_session, user_id)
        obs = await agent.observe()

        assert obs.total_topics == 3
        assert len(obs.weak_topics) >= 1  # Calculus should be weak
        assert obs.days_until_next_exam is not None

    @pytest.mark.asyncio
    async def test_plan_recommends_boost(self, db_session: AsyncSession):
        user_id = await _seed(db_session)
        agent = AdaptiveAgent(db_session, user_id)
        await agent.observe()
        plan = await agent.plan_actions()

        assert len(plan.boost_topics) >= 1
        assert any("weak" in m.lower() for m in plan.messages)

    @pytest.mark.asyncio
    async def test_full_loop_completes(self, db_session: AsyncSession):
        user_id = await _seed(db_session)
        agent = AdaptiveAgent(db_session, user_id)
        reflection = await agent.run()

        assert reflection is not None
        assert len(reflection.insights) > 0
