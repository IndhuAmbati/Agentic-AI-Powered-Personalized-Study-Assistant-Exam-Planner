import asyncio
import sys
from datetime import date, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.app.database import Base, async_session_factory, engine
from backend.app.main import app
from backend.app.models.subject import Subject
from backend.app.models.user import User


async def build_payload(user_id: str) -> dict:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError(f"User not found: {user_id}")

        subject_result = await session.execute(
            select(Subject).where(Subject.user_id == user_id)
        )
        subjects = subject_result.scalars().all()

    today = date.today()
    exam_dates = [subject.exam_date for subject in subjects if subject.exam_date]
    end_date = min(exam_dates) if exam_dates else today + timedelta(days=30)
    if end_date <= today:
        end_date = today + timedelta(days=30)

    daily_hours = float(user.daily_study_hours or 4.0)
    session_duration = 45 if daily_hours <= 2 else 60 if daily_hours <= 5 else 90
    break_duration = max(10, min(20, session_duration // 4))
    daily_start_hour = 7 if user.learning_preference == "practice" else 8

    return {
        "user_id": user_id,
        "start_date": today.isoformat(),
        "end_date": end_date.isoformat(),
        "daily_start_time": f"{daily_start_hour:02d}:00:00",
        "daily_study_hours": daily_hours,
        "session_duration_mins": session_duration,
        "break_duration_mins": break_duration,
    }


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python _tmp_sched.py <user_id>")

    user_id = sys.argv[1].strip()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    payload = await build_payload(user_id)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/schedule/generate", json=payload)
        print("payload", payload)
        print("status", response.status_code)
        print(response.text[:400])


asyncio.run(main())
