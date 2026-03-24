"""Tests for mind map payload enrichment."""

from __future__ import annotations

from datetime import date

from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.services.mind_map import build_mind_map


def test_build_mind_map_adds_unit_learning_cues():
    subject = Subject(
        id="subject-1",
        user_id="user-1",
        name="AI401PC Discrete Mathematics",
        exam_date=date(2026, 4, 10),
        priority=4.0,
        color="#4A90D9",
    )
    topics = [
        Topic(
            id="topic-1",
            subject_id=subject.id,
            name="Unit 1: Theory of Inference for the Statement Calculus",
            completion_pct=100.0,
            completed=1,
            estimated_hours=1.0,
            revision_count=1,
            difficulty=0.6,
            order_index=0,
        ),
        Topic(
            id="topic-2",
            subject_id=subject.id,
            name="Unit 1: Rules of Inference",
            completion_pct=35.0,
            completed=0,
            estimated_hours=1.5,
            revision_count=0,
            difficulty=0.7,
            order_index=1,
        ),
    ]
    subject.topics.extend(topics)

    payload = build_mind_map([subject], topics)

    unit = payload["subjects"][0]["units"][0]
    assert payload["subjects"][0]["snapshot"]
    assert unit["headline"]
    assert unit["learning_goal"]
    assert unit["study_hint"]
    assert unit["focus_points"]
    assert unit["topics"][0]["status_label"] in {"Done", "Strong progress", "Needs focus"}


def test_build_mind_map_marks_weak_topic_after_three_quiz_attempts_below_75():
    subject = Subject(
        id="subject-1",
        user_id="user-1",
        name="AI402PC Automata Theory & Compiler Design",
        exam_date=date(2026, 4, 10),
        priority=4.0,
        color="#4A90D9",
    )
    topic = Topic(
        id="topic-weak",
        subject_id=subject.id,
        name="Unit 1: Introduction to Finite Automata",
        completion_pct=100.0,
        completed=1,
        estimated_hours=2.0,
        revision_count=1,
        difficulty=0.7,
        order_index=0,
    )
    subject.topics.append(topic)

    payload = build_mind_map(
        [subject],
        [topic],
        {
            "topic-weak": {
                "attempts": 3,
                "average_score": 72.0,
            }
        },
    )

    built_subject = payload["subjects"][0]
    built_unit = built_subject["units"][0]
    built_topic = built_unit["topics"][0]
    assert built_subject["weak_topic_count"] == 1
    assert built_unit["weak_topic_count"] == 1
    assert built_topic["is_weak"] is True
    assert built_topic["status_label"] == "Weak area"
