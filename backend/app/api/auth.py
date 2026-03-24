"""Authentication API routes."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models.auth import AuthCredential
from backend.app.models.user import User
from backend.app.schemas.auth import RegisterRequest, LoginRequest, AuthResponse
from backend.app.services.email_service import send_welcome_email

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    ).hex()


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a login credential + user profile."""
    email = payload.email.strip().lower()

    existing_credential = await db.execute(
        select(AuthCredential).where(AuthCredential.email == email)
    )
    if existing_credential.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    existing_user_result = await db.execute(select(User).where(User.email == email))
    user = existing_user_result.scalar_one_or_none()

    # Backward compatibility:
    # if user exists from old flow (without credentials), attach credentials to it.
    if user is None:
        user = User(
            name=payload.name.strip(),
            email=email,
            daily_study_hours=payload.daily_study_hours,
            learning_preference=payload.learning_preference,
            difficulty_level=payload.difficulty_level,
        )
        db.add(user)
        await db.flush()
    else:
        user.name = payload.name.strip() or user.name
        user.daily_study_hours = payload.daily_study_hours
        user.learning_preference = payload.learning_preference
        user.difficulty_level = payload.difficulty_level

    salt = secrets.token_hex(16)
    db.add(
        AuthCredential(
            user_id=user.id,
            email=email,
            password_salt=salt,
            password_hash=_hash_password(payload.password, salt),
        )
    )
    await db.flush()
    await db.refresh(user)
    background_tasks.add_task(send_welcome_email, user)

    return {"user": user}


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user with email + password."""
    email = payload.email.strip().lower()
    result = await db.execute(select(AuthCredential).where(AuthCredential.email == email))
    credential = result.scalar_one_or_none()
    if not credential:
        raise HTTPException(401, "Invalid email or password")

    computed = _hash_password(payload.password, credential.password_salt)
    if not hmac.compare_digest(computed, credential.password_hash):
        raise HTTPException(401, "Invalid email or password")

    user = await db.get(User, credential.user_id)
    if not user:
        raise HTTPException(404, "User profile not found")

    return {"user": user}
