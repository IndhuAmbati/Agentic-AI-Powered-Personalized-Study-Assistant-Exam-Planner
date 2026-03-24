"""Authentication schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.app.schemas.user import UserOut

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    daily_study_hours: float = Field(4.0, ge=0.5, le=16.0)
    learning_preference: str = Field("balanced")
    difficulty_level: str = Field("medium")

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)

class AuthResponse(BaseModel):
    user: UserOut
