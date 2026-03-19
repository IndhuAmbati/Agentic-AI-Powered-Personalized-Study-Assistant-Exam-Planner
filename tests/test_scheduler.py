"""Tests for the core scheduling algorithm."""

from __future__ import annotations

from datetime import date, time, datetime, timezone

from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.services.scheduler import compute_priority, generate_schedule


def _make_subject(name: str, exam_date: date | None = None, priority: float = 3.0) -> Subject:
    """Helper to create an in-memory Subject with topics list."""
    return Subject(
        id=f"subj-{name}",
        user_id="user-1",
        name=name,
        exam_date=exam_date,
        priority=priority,
        color="#aaa",
    )


def _make_topic(
    name: str,
    subject: Subject,
    difficulty: float = 0.5,
    completion_pct: float = 0.0,
    estimated_hours: float = 2.0,
    completed: int = 0,
    last_reviewed: datetime | None = None,
    time_spent_mins: float = 0.0
) -> Topic:
    t = Topic(
        id=f"topic-{name}",
        subject_id=subject.id,
        name=name,
        difficulty=difficulty,
        completion_pct=completion_pct,
        estimated_hours=estimated_hours,
        completed=completed,
        last_reviewed=last_reviewed,
        time_spent_mins=time_spent_mins,
        revision_count=0,
        order_index=0,
    )
    subject.topics.append(t)
    return t


class TestComputePriority:
    """Unit tests for priority scoring."""

    def test_urgency_increases_close_to_exam(self):
        """Topics with a nearer exam date should score higher."""
        s_near = _make_subject("Math", exam_date=date(2026, 2, 15))
        s_far = _make_subject("History", exam_date=date(2026, 6, 15))
        t_near = _make_topic("Algebra", s_near)
        t_far = _make_topic("WW2", s_far)

        p_near = compute_priority(t_near, s_near, reference_date=date(2026, 2, 10))
        p_far = compute_priority(t_far, s_far, reference_date=date(2026, 2, 10))

        assert p_near.priority_score > p_far.priority_score

    def test_harder_topics_score_higher(self):
        s = _make_subject("Physics", exam_date=date(2026, 4, 1))
        t_easy = _make_topic("Basics", s, difficulty=0.1)
        t_hard = _make_topic("Quantum", s, difficulty=0.9)

        p_easy = compute_priority(t_easy, s, reference_date=date(2026, 2, 10))
        p_hard = compute_priority(t_hard, s, reference_date=date(2026, 2, 10))

        assert p_hard.priority_score > p_easy.priority_score

    def test_incomplete_topics_prioritised(self):
        s = _make_subject("Bio", exam_date=date(2026, 4, 1))
        t_done = _make_topic("Cells", s, completion_pct=100)
        t_todo = _make_topic("Genetics", s, completion_pct=10)

        p_done = compute_priority(t_done, s, reference_date=date(2026, 2, 10))
        p_todo = compute_priority(t_todo, s, reference_date=date(2026, 2, 10))

        assert p_todo.priority_score > p_done.priority_score


class TestGenerateSchedule:
    """Integration tests for schedule generation."""

    def test_generates_blocks(self):
        s = _make_subject("CS", exam_date=date(2026, 3, 1), priority=4.0)
        _make_topic("Algorithms", s, estimated_hours=5)
        _make_topic("Data Structures", s, estimated_hours=3)

        blocks = generate_schedule(
            user_id="user-1",
            subjects=[s],
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 14),
            daily_hours=3.0,
            daily_start=time(9, 0),
            session_mins=60,
            break_mins=15,
        )
        assert len(blocks) > 0
        assert all(b.duration_mins == 60 for b in blocks)

    def test_respects_daily_hours(self):
        s = _make_subject("Art", exam_date=date(2026, 5, 1))
        _make_topic("Drawing", s, estimated_hours=20)

        blocks = generate_schedule(
            user_id="user-1",
            subjects=[s],
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 10),
            daily_hours=2.0,
            session_mins=45,
            break_mins=15,
        )
        total_mins = sum(b.duration_mins for b in blocks)
        assert total_mins <= 2 * 60  # should not exceed daily limit

    def test_empty_topics_returns_empty(self):
        s = _make_subject("Empty")
        blocks = generate_schedule(
            user_id="user-1",
            subjects=[s],
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 12),
        )
        assert blocks == []

    def test_repeats_topic_across_days_until_estimated_hours_are_allocated(self):
        s = _make_subject("Systems", exam_date=date(2026, 3, 1))
        _make_topic("Unit I: Scheduling", s, estimated_hours=3)

        blocks = generate_schedule(
            user_id="user-1",
            subjects=[s],
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 12),
            daily_hours=1.0,
            session_mins=60,
            break_mins=15,
            avoid_topic_repeats=True,
            distribute_across_range=True,
        )

        assert len(blocks) == 3
        assert [block.scheduled_date for block in blocks] == [
            date(2026, 2, 10),
            date(2026, 2, 11),
            date(2026, 2, 12),
        ]
        assert all(block.topic_name == "Unit I: Scheduling" for block in blocks)
