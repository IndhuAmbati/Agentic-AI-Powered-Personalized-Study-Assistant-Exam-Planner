"""Tests for quiz generation parsing and validation."""

from __future__ import annotations

import json

import pytest
from types import SimpleNamespace
from sqlalchemy import select

from backend.app.services import quiz_engine
from backend.app.models.user import User
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.models.quiz import Quiz, QuizQuestion
from backend.app.models.progress import ProgressRecord
from backend.app.schemas.quiz import QuizSubmitRequest


def _valid_question(question_text: str) -> dict[str, str]:
    return {
        "question_text": question_text,
        "option_a": "A transition rule for each symbol",
        "option_b": "Only a list of final states",
        "option_c": "A stack of unmatched symbols",
        "option_d": "A parse tree for each string",
        "correct_answer": "A",
        "explanation": "A DFA transition function maps each state and input symbol to one next state.",
    }


def test_extract_json_payload_handles_fenced_json():
    content = (
        "Here is the quiz.\n"
        "```json\n"
        f"{json.dumps([_valid_question('In finite automata, what does a DFA transition function define?')])}\n"
        "```\n"
        "Use it directly."
    )

    parsed = quiz_engine._extract_json_payload(content)

    assert isinstance(parsed, list)
    assert parsed[0]["correct_answer"] == "A"


def test_groq_max_tokens_scales_with_question_count():
    assert quiz_engine._groq_max_tokens_for_quiz(1) == 450
    assert quiz_engine._groq_max_tokens_for_quiz(8) > 800
    assert quiz_engine._groq_max_tokens_for_quiz(40) == 1400


def test_extract_retry_hint_reads_groq_wait_time():
    detail = (
        'Rate limit reached for model `llama-3.3-70b-versatile` '
        'Please try again in 2m31. More details.'
    )

    parsed = quiz_engine._extract_retry_hint(detail)

    assert parsed == "2m31"


@pytest.mark.asyncio
async def test_generate_questions_with_groq_parses_fenced_response(monkeypatch: pytest.MonkeyPatch):
    captured_payload: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, content: str):
            self._data = {
                "choices": [
                    {
                        "message": {
                            "content": content,
                        }
                    }
                ]
            }
            self.text = json.dumps(self._data)

        def json(self) -> dict[str, object]:
            return self._data

        def raise_for_status(self) -> None:
            return None

    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            captured_payload.update(json)
            question = _valid_question(
                "In finite automata, what does the transition function specify for a DFA?"
            )
            return DummyResponse(f"```json\n{json_module.dumps([question])}\n```")

    json_module = json
    monkeypatch.setattr(quiz_engine.settings, "groq_api_key", "test-key")
    monkeypatch.setattr(quiz_engine.httpx, "AsyncClient", DummyAsyncClient)

    questions = await quiz_engine._generate_questions_with_groq(
        topic_name="Unit 1: Introduction to Finite Automata",
        difficulty="easy",
        count=8,
        related_topics=["Regular Languages"],
        used_questions=set(),
        subject_name="Theory of Computation",
    )

    assert len(questions) == 1
    assert questions[0]["question_text"].startswith("In finite automata")
    assert captured_payload["max_tokens"] == quiz_engine._groq_max_tokens_for_quiz(8)


@pytest.mark.asyncio
async def test_create_quiz_does_not_use_local_fallback_when_disabled(monkeypatch: pytest.MonkeyPatch):
    topic = SimpleNamespace(id="topic-1", name="Database Management Systems", subject_id="subject-1")
    subject = SimpleNamespace(id="subject-1", name="Data Analytics")

    class DummyScalars:
        def all(self):
            return []

    class DummyResult:
        def scalars(self):
            return DummyScalars()

        def scalar_one(self):
            raise AssertionError("Quiz should not be persisted when Groq generation fails")

    class DummySession:
        async def get(self, model, ident):
            model_name = getattr(model, "__name__", "")
            if model_name == "Topic":
                return topic if ident == topic.id else None
            if model_name == "Subject":
                return subject if ident == subject.id else None
            return None

        async def execute(self, stmt):
            return DummyResult()

        def add(self, obj):
            raise AssertionError("Should not add quiz rows on generation failure")

        async def flush(self):
            raise AssertionError("Should not flush quiz rows on generation failure")

    async def fake_llm(*args, **kwargs):
        raise RuntimeError("Groq unavailable")

    monkeypatch.setattr(quiz_engine, "_generate_questions_with_llm", fake_llm)
    monkeypatch.setattr(quiz_engine.settings, "allow_groq_fallback", False)

    with pytest.raises(RuntimeError, match="Groq could not prepare enough valid quiz questions"):
        await quiz_engine.create_quiz(
            DummySession(),
            quiz_engine.QuizGenerateRequest(
                user_id="user-1",
                topic_id="topic-1",
                difficulty="medium",
                num_questions=5,
            ),
        )


@pytest.mark.asyncio
async def test_evaluate_quiz_updates_topic_progress_record_with_new_completion(db_session, monkeypatch: pytest.MonkeyPatch):
    user = User(name="Test User", email="quiz-progress@example.com")
    db_session.add(user)
    await db_session.flush()

    subject = Subject(user_id=user.id, name="Computer Science")
    db_session.add(subject)
    await db_session.flush()

    topic = Topic(
        subject_id=subject.id,
        name="linkedlist",
        completion_pct=40.0,
        time_spent_mins=15.0,
        completed=0,
    )
    db_session.add(topic)
    await db_session.flush()

    quiz = Quiz(user_id=user.id, topic_id=topic.id, difficulty="medium", total_questions=1)
    db_session.add(quiz)
    await db_session.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="What is a linked list node?",
        option_a="An element with data and a pointer",
        option_b="A sorting algorithm",
        option_c="A SQL table",
        option_d="A graph traversal",
        correct_answer="A",
        explanation="A node stores data and a reference to the next node.",
        order_index=0,
    )
    db_session.add(question)
    await db_session.flush()

    async def fake_record_quiz_performance(*args, **kwargs):
        return None

    async def fake_create_quiz(*args, **kwargs):
        raise RuntimeError("skip next quiz generation in test")

    monkeypatch.setattr(quiz_engine, "_record_quiz_performance", fake_record_quiz_performance)
    monkeypatch.setattr(quiz_engine, "create_quiz", fake_create_quiz)

    result = await quiz_engine.evaluate_quiz(
        db_session,
        QuizSubmitRequest(
            quiz_id=quiz.id,
            user_id=user.id,
            answers=[{"question_id": question.id, "answer": "A"}],
        ),
    )

    await db_session.flush()
    await db_session.refresh(topic)

    progress_records = list(
        (
            await db_session.execute(
                select(ProgressRecord).where(ProgressRecord.topic_id == topic.id)
            )
        ).scalars().all()
    )

    assert result.passed is True
    assert topic.completion_pct == 45.0
    assert topic.time_spent_mins == 25.0
    assert len(progress_records) == 1
    assert progress_records[0].completion_pct == 45.0
    assert progress_records[0].time_spent_mins == 10.0
    assert progress_records[0].quiz_score == 100.0


@pytest.mark.asyncio
async def test_evaluate_custom_quiz_does_not_update_progress(db_session, monkeypatch: pytest.MonkeyPatch):
    user = User(name="Custom User", email="custom-quiz@example.com")
    db_session.add(user)
    await db_session.flush()

    subject = Subject(user_id=user.id, name="General")
    db_session.add(subject)
    await db_session.flush()

    topic = Topic(
        subject_id=subject.id,
        name="linkedlist",
        completion_pct=40.0,
        time_spent_mins=15.0,
        completed=0,
    )
    db_session.add(topic)
    await db_session.flush()

    quiz = Quiz(user_id=user.id, topic_id=topic.id, difficulty="medium", total_questions=1)
    db_session.add(quiz)
    await db_session.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="What is a linked list?",
        option_a="A linear data structure with nodes",
        option_b="A database index",
        option_c="A sorting method",
        option_d="A stack frame",
        correct_answer="A",
        explanation="A linked list stores elements as nodes connected by links.",
        order_index=0,
    )
    db_session.add(question)
    await db_session.flush()

    async def fake_record_quiz_performance(*args, **kwargs):
        raise AssertionError("Custom quizzes should not update quiz performance analytics")

    monkeypatch.setattr(quiz_engine, "_record_quiz_performance", fake_record_quiz_performance)

    result = await quiz_engine.evaluate_quiz(
        db_session,
        QuizSubmitRequest(
            quiz_id=quiz.id,
            user_id=user.id,
            include_in_progress=False,
            answers=[{"question_id": question.id, "answer": "A"}],
        ),
    )

    await db_session.flush()
    await db_session.refresh(topic)
    await db_session.refresh(quiz)

    progress_records = list(
        (
            await db_session.execute(
                select(ProgressRecord).where(ProgressRecord.topic_id == topic.id)
            )
        ).scalars().all()
    )

    assert result.passed is True
    assert result.recommendation == "Custom quiz score saved. Study progress was not changed for this topic."
    assert quiz.score == 100.0
    assert topic.completion_pct == 40.0
    assert topic.time_spent_mins == 15.0
    assert progress_records == []
