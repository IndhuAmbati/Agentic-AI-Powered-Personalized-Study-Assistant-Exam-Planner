"""
Progress Tracking Service
=========================
Handles recording and querying student progress data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.topic import Topic
from backend.app.models.progress import ProgressRecord
from backend.app.models.quiz import Quiz
from backend.app.models.schedule import ScheduleEntry
from backend.app.schemas.quiz import QuizGenerateRequest
from backend.app.services.quiz_engine import create_quiz
from backend.app.schemas.progress import ProgressUpdate, ProgressDashboard

PASS_THRESHOLDS = {
    "easy": 75.0,
    "medium": 75.0,
    "hard": 75.0,
}


async def record_progress(db: AsyncSession, data: ProgressUpdate) -> ProgressRecord:
    """Create a progress snapshot and update the topic cumulative fields."""
    # Create a new progress record
    record = ProgressRecord(
        user_id=data.user_id,
        topic_id=data.topic_id,
        completion_pct=data.completion_pct,
        time_spent_mins=data.time_spent_mins,
        quiz_score=data.quiz_score,
        notes=data.notes,
    )
    db.add(record)

    # Update the topic row
    topic = await db.get(Topic, data.topic_id)
    if topic:
        topic.completion_pct = data.completion_pct
        topic.time_spent_mins += data.time_spent_mins
        topic.last_reviewed = datetime.now(timezone.utc)
        if data.completion_pct >= 100:
            topic.completed = 1
            topic.revision_count += 1
    await db.flush()
    return record


async def get_dashboard(db: AsyncSession, user_id: str) -> ProgressDashboard:
    """Build an aggregated progress dashboard for a user."""
    # Fetch all topics for the user (through subjects)
    from backend.app.models.subject import Subject

    subj_q = select(Subject.id).where(Subject.user_id == user_id)
    topics_q = select(Topic).where(Topic.subject_id.in_(subj_q))
    result = await db.execute(topics_q)
    topics: list[Topic] = list(result.scalars().all())

    total = len(topics)
    completed = sum(1 for t in topics if t.completed)
    overall_pct = sum(t.completion_pct for t in topics) / total if total else 0.0
    total_time = sum(t.time_spent_mins for t in topics)
    topics_covered = [t.name for t in topics if t.completed or t.completion_pct >= 100][:8]
    topic_names = {topic.id: topic.name for topic in topics}

    # Average quiz score from progress records
    score_q = select(func.avg(ProgressRecord.quiz_score)).where(
        ProgressRecord.user_id == user_id,
        ProgressRecord.quiz_score.isnot(None),
    )
    avg_score_result = await db.execute(score_q)
    avg_score = avg_score_result.scalar()

    upcoming = [t.name for t in topics if t.completion_pct < 100 and not t.completed][:10]

    quiz_q = select(Quiz).where(
        Quiz.user_id == user_id,
        Quiz.score.isnot(None),
        Quiz.included_in_progress.is_(True),
    )
    quiz_result = await db.execute(quiz_q)
    quizzes = list(quiz_result.scalars().all())
    by_topic: dict[str, int] = {}
    topic_scores: dict[str, list[float]] = {}
    passed_quizzes = 0
    for quiz in quizzes:
        difficulty = (quiz.difficulty or "medium").lower()
        threshold = PASS_THRESHOLDS.get(
            difficulty,
            PASS_THRESHOLDS["medium"],
        )
        if (quiz.score or 0) >= threshold:
            passed_quizzes += 1
            by_topic[quiz.topic_id] = by_topic.get(quiz.topic_id, 0) + 1
        if quiz.score is not None:
            topic_scores.setdefault(quiz.topic_id, []).append(float(quiz.score))

    weak_topics: list[str] = []
    topic_quiz_averages: list[dict] = []
    for topic in topics:
        scores = topic_scores.get(topic.id, [])
        if not scores:
            continue
        average = sum(scores) / len(scores)
        topic_quiz_averages.append(
            {
                "topic_id": topic.id,
                "topic_name": topic.name,
                "average_score": round(average, 1),
                "attempts": len(scores),
                "completion_pct": round(float(topic.completion_pct or 0.0), 1),
            }
        )
        if average < 75.0:
            weak_topics.append(f"{topic.name} ({round(average, 1)}%)")

    topic_quiz_averages.sort(
        key=lambda item: (item["average_score"], -item["attempts"], item["topic_name"].lower())
    )

    not_started_topics = sum(1 for topic in topics if (topic.completion_pct or 0) <= 0)
    in_progress_topics = sum(
        1 for topic in topics if 0 < (topic.completion_pct or 0) < 100 and not topic.completed
    )
    topic_status_breakdown = [
        {"name": "Completed", "value": completed},
        {"name": "In progress", "value": in_progress_topics},
        {"name": "Not started", "value": not_started_topics},
    ]

    schedule_q = select(ScheduleEntry).where(ScheduleEntry.user_id == user_id)
    schedule_result = await db.execute(schedule_q)
    schedule_entries = list(schedule_result.scalars().all())
    total_schedule_entries = len(schedule_entries)
    completed_schedule_entries = sum(1 for entry in schedule_entries if entry.completed)

    mastery_pending: list[dict] = []
    for topic in topics:
        if topic.completion_pct < 100:
            continue
        passed = by_topic.get(topic.id, 0)
        if passed < 3:
            mastery_pending.append(
                {
                    "topic_id": topic.id,
                    "topic_name": topic.name,
                    "passed_quizzes": passed,
                    "required_quizzes": 3,
                }
            )

    return ProgressDashboard(
        user_id=user_id,
        total_topics=total,
        completed_topics=completed,
        overall_completion_pct=round(overall_pct, 1),
        total_time_spent_mins=round(total_time, 1),
        average_quiz_score=round(avg_score, 1) if avg_score is not None else None,
        total_schedule_entries=total_schedule_entries,
        completed_schedule_entries=completed_schedule_entries,
        total_quizzes_taken=len(quizzes),
        quizzes_passed=passed_quizzes,
        quizzes_failed=max(len(quizzes) - passed_quizzes, 0),
        weak_topics=weak_topics[:8],
        topics_covered=topics_covered,
        upcoming_topics=upcoming,
        mastery_pending=mastery_pending,
        topic_status_breakdown=topic_status_breakdown,
        topic_quiz_averages=topic_quiz_averages[:8],
    )
