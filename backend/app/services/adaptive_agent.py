"""
Adaptive AI Agent
=================
Implements the **Observe → Plan → Act → Reflect** agentic loop that
continuously improves the student's study plan.

The agent runs as a service function invoked by the API or a background job.
It evaluates progress, detects weak areas, and regenerates the schedule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.user import User
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.models.schedule import ScheduleEntry
from backend.app.models.progress import ProgressRecord
from backend.app.services.scheduler import generate_schedule, blocks_to_entries

logger = logging.getLogger(__name__)


# ── Agent Data Structures ───────────────────────────────────────────

@dataclass
class Observation:
    """Snapshot of the student's state for the agent to reason about."""
    user_id: str
    total_topics: int = 0
    completed_topics: int = 0
    overall_progress_pct: float = 0.0
    weak_topics: list[dict[str, Any]] = field(default_factory=list)
    overdue_topics: list[dict[str, Any]] = field(default_factory=list)
    avg_quiz_score: float | None = None
    days_until_next_exam: int | None = None


@dataclass
class Plan:
    """Actions the agent intends to take."""
    reschedule: bool = False
    boost_topics: list[str] = field(default_factory=list)   # topic IDs to prioritise
    reduce_topics: list[str] = field(default_factory=list)  # topic IDs to de-prioritise
    suggested_quizzes: list[str] = field(default_factory=list)  # topic IDs
    messages: list[str] = field(default_factory=list)


@dataclass
class Reflection:
    """Post-action evaluation."""
    schedule_entries_created: int = 0
    topics_boosted: int = 0
    insights: list[str] = field(default_factory=list)


# ── Agent Core ──────────────────────────────────────────────────────

class AdaptiveAgent:
    """
    Agentic loop that adapts the study plan for a single user.

    Usage::

        agent = AdaptiveAgent(db, user_id)
        reflection = await agent.run()
    """

    def __init__(self, db: AsyncSession, user_id: str):
        self.db = db
        self.user_id = user_id
        self.observation: Observation | None = None
        self.plan: Plan | None = None
        self.reflection: Reflection | None = None

    # ── Phase 1: Observe ────────────────────────────────────────────

    async def observe(self) -> Observation:
        """Gather all relevant metrics for the user."""
        obs = Observation(user_id=self.user_id)

        # Load subjects + topics
        q = (
            select(Subject)
            .where(Subject.user_id == self.user_id)
            .options(selectinload(Subject.topics))
        )
        result = await self.db.execute(q)
        subjects: list[Subject] = list(result.scalars().unique().all())

        all_topics: list[Topic] = []
        nearest_exam_days: int | None = None
        today = date.today()

        for subj in subjects:
            for t in subj.topics:
                all_topics.append(t)
            if subj.exam_date:
                d = (subj.exam_date - today).days
                if nearest_exam_days is None or d < nearest_exam_days:
                    nearest_exam_days = d

        obs.total_topics = len(all_topics)
        obs.completed_topics = sum(1 for t in all_topics if t.completed)
        obs.overall_progress_pct = (
            sum(t.completion_pct for t in all_topics) / obs.total_topics
            if obs.total_topics
            else 0.0
        )
        obs.days_until_next_exam = nearest_exam_days

        # Weak topics: low completion AND high difficulty
        for t in all_topics:
            if t.completion_pct < 40 and t.difficulty >= 0.5:
                obs.weak_topics.append({"id": t.id, "name": t.name, "completion": t.completion_pct})

        # Overdue: scheduled entries in the past that are not completed
        overdue_q = select(ScheduleEntry).where(
            ScheduleEntry.user_id == self.user_id,
            ScheduleEntry.scheduled_date < today,
            ScheduleEntry.completed == 0,
        )
        overdue_result = await self.db.execute(overdue_q)
        for entry in overdue_result.scalars().all():
            obs.overdue_topics.append({"topic_id": entry.topic_id, "date": str(entry.scheduled_date)})

        # Avg quiz score
        from sqlalchemy import func as sqlfunc
        score_q = select(sqlfunc.avg(ProgressRecord.quiz_score)).where(
            ProgressRecord.user_id == self.user_id,
            ProgressRecord.quiz_score.isnot(None),
        )
        avg_result = await self.db.execute(score_q)
        obs.avg_quiz_score = avg_result.scalar()

        self.observation = obs
        logger.info("Agent OBSERVE complete for user %s", self.user_id)
        return obs

    # ── Phase 2: Plan ───────────────────────────────────────────────

    async def plan_actions(self) -> Plan:
        """Decide what adjustments to make based on observations."""
        assert self.observation is not None, "Call observe() first"
        obs = self.observation
        plan = Plan()

        # Rule 1: If weak topics exist, boost them
        if obs.weak_topics:
            plan.boost_topics = [t["id"] for t in obs.weak_topics]
            plan.messages.append(
                f"Detected {len(obs.weak_topics)} weak topic(s) — increasing study allocation."
            )
            plan.suggested_quizzes = plan.boost_topics[:3]

        # Rule 2: If overdue items exist, reschedule
        if obs.overdue_topics:
            plan.reschedule = True
            plan.messages.append(
                f"{len(obs.overdue_topics)} overdue session(s) detected — rescheduling."
            )

        # Rule 3: If exams are imminent (< 7 days), reschedule aggressively
        if obs.days_until_next_exam is not None and obs.days_until_next_exam <= 7:
            plan.reschedule = True
            plan.messages.append(
                f"Exam in {obs.days_until_next_exam} day(s) — switching to intensive mode."
            )

        # Rule 4: If average quiz score is low, suggest more quizzes
        if obs.avg_quiz_score is not None and obs.avg_quiz_score < 60:
            plan.messages.append(
                "Average quiz score is below 60 % — recommending more practice quizzes."
            )

        # Rule 5: If overall progress is high, suggest revision
        if obs.overall_progress_pct > 80:
            plan.messages.append(
                "Great progress! Shifting focus to revision and practice tests."
            )

        if not plan.messages:
            plan.messages.append("Everything looks on track — no changes needed.")

        self.plan = plan
        logger.info("Agent PLAN complete for user %s", self.user_id)
        return plan

    # ── Phase 3: Act ────────────────────────────────────────────────

    async def act(self) -> None:
        """Execute the planned adjustments."""
        assert self.plan is not None, "Call plan_actions() first"
        plan = self.plan

        # Boost weak topic difficulties to raise their scheduling priority
        for topic_id in plan.boost_topics:
            topic = await self.db.get(Topic, topic_id)
            if topic:
                topic.difficulty = min(topic.difficulty + 0.15, 1.0)

        # Regenerate schedule if needed
        if plan.reschedule:
            # Remove future incomplete entries
            today = date.today()
            future_q = select(ScheduleEntry).where(
                ScheduleEntry.user_id == self.user_id,
                ScheduleEntry.scheduled_date >= today,
                ScheduleEntry.completed == 0,
            )
            result = await self.db.execute(future_q)
            for entry in result.scalars().all():
                await self.db.delete(entry)

            # Reload subjects
            subj_q = (
                select(Subject)
                .where(Subject.user_id == self.user_id)
                .options(selectinload(Subject.topics))
            )
            subj_result = await self.db.execute(subj_q)
            subjects = list(subj_result.scalars().unique().all())

            # Get user's daily hours
            user = await self.db.get(User, self.user_id)
            daily_hours = user.daily_study_hours if user else 4.0

            # Determine end date (nearest exam + 7 days buffer, or 30 days)
            end = today
            for s in subjects:
                if s.exam_date and s.exam_date > today:
                    end = max(end, s.exam_date)
            if end == today:
                from datetime import timedelta
                end = today + timedelta(days=30)

            blocks = generate_schedule(
                user_id=self.user_id,
                subjects=subjects,
                start_date=today,
                end_date=end,
                daily_hours=daily_hours,
            )
            entries = blocks_to_entries(self.user_id, blocks)
            self.db.add_all(entries)
            self._entries_created = len(entries)
        else:
            self._entries_created = 0

        await self.db.flush()
        logger.info("Agent ACT complete for user %s", self.user_id)

    # ── Phase 4: Reflect ────────────────────────────────────────────

    async def reflect(self) -> Reflection:
        """Evaluate the results of the action phase."""
        assert self.plan is not None
        ref = Reflection(
            schedule_entries_created=getattr(self, "_entries_created", 0),
            topics_boosted=len(self.plan.boost_topics),
            insights=list(self.plan.messages),
        )
        self.reflection = ref
        logger.info("Agent REFLECT complete for user %s", self.user_id)
        return ref

    # ── Full Loop ───────────────────────────────────────────────────

    async def run(self) -> Reflection:
        """Execute the full Observe → Plan → Act → Reflect loop."""
        await self.observe()
        await self.plan_actions()
        await self.act()
        return await self.reflect()
