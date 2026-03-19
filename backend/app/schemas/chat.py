"""Chatbot schemas."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=4000)


class ChatAskRequest(BaseModel):
    user_id: str
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)


class ChatAskResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    mode: str = "rule_based"


class ChatHistoryItem(BaseModel):
    id: str
    role: str
    content: str
    mode: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
