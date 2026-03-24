from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.services import progress


class _DummyScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _DummyResult:
    def __init__(self, *, scalar_value=None, scalars_values=None):
        self._scalar_value = scalar_value
        self._scalars_values = scalars_values

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return _DummyScalars(self._scalars_values or [])


class _DummySession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, stmt):
        if not self._results:
            raise AssertionError("Unexpected extra query")
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_get_dashboard_keeps_zero_quiz_average_and_aligned_pass_thresholds():
    topic = SimpleNamespace(
        id="topic-1",
        name="Unit 1: Data Management",
        completed=1,
        completion_pct=100.0,
        time_spent_mins=120.0,
    )
    quiz = SimpleNamespace(
        topic_id="topic-1",
        difficulty="easy",
        score=70.0,
    )
    schedule_entry = SimpleNamespace(completed=1)
    db = _DummySession(
        [
            _DummyResult(scalars_values=[topic]),
            _DummyResult(scalar_value=0.0),
            _DummyResult(scalars_values=[quiz]),
            _DummyResult(scalars_values=[schedule_entry]),
        ]
    )

    dashboard = await progress.get_dashboard(db, "user-1")

    assert dashboard.average_quiz_score == 0.0
    assert dashboard.total_quizzes_taken == 1
    assert dashboard.quizzes_passed == 0
    assert dashboard.mastery_pending[0]["passed_quizzes"] == 0
