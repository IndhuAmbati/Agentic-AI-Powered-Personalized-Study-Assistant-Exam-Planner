"""Tests for the adaptive agent logic."""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.schedule import ScheduleEntry
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

    @pytest.mark.asyncio
    async def test_reschedule_rebuilds_overdue_pending_entries_from_today(
        self,
        db_session: AsyncSession,
    ):
        user_id = await _seed(db_session)

        subject_result = await db_session.execute(select(Subject).where(Subject.user_id == user_id))
        subject = subject_result.scalar_one()
        topic_result = await db_session.execute(
            select(Topic).where(Topic.subject_id == subject.id).order_by(Topic.name)
        )
        topics = list(topic_result.scalars().all())
        today = date.today()

        overdue_entry = ScheduleEntry(
            user_id=user_id,
            topic_id=topics[0].id,
            subject_name=subject.name,
            topic_name=topics[0].name,
            scheduled_date=today - timedelta(days=1),
            start_time=datetime.strptime("09:00", "%H:%M").time(),
            end_time=datetime.strptime("10:00", "%H:%M").time(),
            duration_mins=60,
            priority_score=0.8,
            is_revision=0,
            completed=0,
        )
        future_entry = ScheduleEntry(
            user_id=user_id,
            topic_id=topics[1].id,
            subject_name=subject.name,
            topic_name=topics[1].name,
            scheduled_date=today + timedelta(days=1),
            start_time=datetime.strptime("11:00", "%H:%M").time(),
            end_time=datetime.strptime("12:00", "%H:%M").time(),
            duration_mins=60,
            priority_score=0.7,
            is_revision=0,
            completed=0,
        )
        completed_entry = ScheduleEntry(
            user_id=user_id,
            topic_id=topics[2].id,
            subject_name=subject.name,
            topic_name=topics[2].name,
            scheduled_date=today - timedelta(days=2),
            start_time=datetime.strptime("14:00", "%H:%M").time(),
            end_time=datetime.strptime("15:00", "%H:%M").time(),
            duration_mins=60,
            priority_score=0.6,
            is_revision=0,
            completed=1,
        )
        db_session.add_all([overdue_entry, future_entry, completed_entry])
        await db_session.flush()

        agent = AdaptiveAgent(db_session, user_id)
        reflection = await agent.run()

        entries_result = await db_session.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.user_id == user_id)
            .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
        )
        entries = list(entries_result.scalars().all())

        assert reflection.schedule_entries_created > 0
        assert all(entry.scheduled_date >= today for entry in entries if entry.completed == 0)
        assert any(entry.completed == 1 for entry in entries)
        assert all(entry.id != overdue_entry.id for entry in entries)
        assert all(entry.id != future_entry.id for entry in entries)

    @pytest.mark.asyncio
    async def test_weak_topics_without_overdue_sessions_do_not_rebuild_schedule(
        self,
        db_session: AsyncSession,
    ):
        user_id = await _seed(db_session)

        subject_result = await db_session.execute(select(Subject).where(Subject.user_id == user_id))
        subject = subject_result.scalar_one()
        topic_result = await db_session.execute(
            select(Topic).where(Topic.subject_id == subject.id).order_by(Topic.name)
        )
        topics = list(topic_result.scalars().all())
        today = date.today()

        future_entry = ScheduleEntry(
            user_id=user_id,
            topic_id=topics[0].id,
            subject_name=subject.name,
            topic_name=topics[0].name,
            scheduled_date=today + timedelta(days=1),
            start_time=datetime.strptime("09:00", "%H:%M").time(),
            end_time=datetime.strptime("10:00", "%H:%M").time(),
            duration_mins=60,
            priority_score=0.8,
            is_revision=0,
            completed=0,
        )
        db_session.add(future_entry)
        await db_session.flush()

        agent = AdaptiveAgent(db_session, user_id)
        reflection = await agent.run()

        entries_result = await db_session.execute(
            select(ScheduleEntry)
            .where(ScheduleEntry.user_id == user_id)
            .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
        )
        entries = list(entries_result.scalars().all())

        assert reflection.schedule_entries_created == 0
        assert len(entries) == 1
        assert entries[0].id == future_entry.id
