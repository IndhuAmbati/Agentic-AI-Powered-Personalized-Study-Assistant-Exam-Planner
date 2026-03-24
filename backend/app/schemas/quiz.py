"""Quiz schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class QuizGenerateRequest(BaseModel):
    user_id: str
    topic_id: str
    difficulty: str = Field("medium")  # easy, medium, hard
    num_questions: int = Field(5, ge=1, le=20)
    use_subject_context: bool = True


class QuizQuestionOut(BaseModel):
    id: str
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    order_index: int

    model_config = {"from_attributes": True}


class QuizOut(BaseModel):
    id: str
    user_id: str
    topic_id: str
    difficulty: str
    total_questions: int
    score: float | None
    created_at: datetime
    questions: list[QuizQuestionOut] = []

    model_config = {"from_attributes": True}


class QuizAnswerItem(BaseModel):
    question_id: str
    answer: str = Field(..., pattern="^[A-D]$")


class QuizSubmitRequest(BaseModel):
    quiz_id: str
    user_id: str
    answers: list[QuizAnswerItem]
    include_in_progress: bool = True


class QuizResult(BaseModel):
    quiz_id: str
    total_questions: int
    correct_count: int
    score_pct: float
    passed: bool
    pass_threshold: float
    recommendation: str
    review_session_created: bool = False
    next_quiz: QuizOut | None = None
    details: list[dict]


class TopicReadyQuizRequest(BaseModel):
    user_id: str
    num_questions: int = Field(5, ge=1, le=20)


class TopicReadyQuizOut(BaseModel):
    topic_id: str
    quizzes: list[QuizOut]
