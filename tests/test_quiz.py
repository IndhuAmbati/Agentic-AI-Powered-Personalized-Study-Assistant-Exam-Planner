"""Tests for the quiz engine."""

from __future__ import annotations

from backend.app.services.quiz_engine import _generate_questions


class TestQuestionGeneration:
    def test_easy_questions(self):
        qs = _generate_questions("Algebra", "easy", 3)
        assert len(qs) == 3
        assert all("Algebra" in q["question_text"] for q in qs)

    def test_medium_questions(self):
        qs = _generate_questions("Physics", "medium", 5)
        assert len(qs) == 5

    def test_hard_questions(self):
        qs = _generate_questions("Chemistry", "hard", 2)
        assert len(qs) == 2
        assert all(q["correct_answer"] in "ABCD" for q in qs)

    def test_unknown_difficulty_defaults_to_medium(self):
        qs = _generate_questions("Bio", "unknown", 3)
        assert len(qs) == 3
