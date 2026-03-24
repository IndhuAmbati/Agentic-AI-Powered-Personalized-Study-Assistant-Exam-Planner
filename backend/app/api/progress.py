"""Progress API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.schemas.progress import ProgressUpdate, ProgressOut, ProgressDashboard
from backend.app.services.progress import record_progress, get_dashboard

router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.post("/update", response_model=ProgressOut, status_code=201)
async def update_progress(payload: ProgressUpdate, db: AsyncSession = Depends(get_db)):
    """Record a progress update for a topic."""
    record = await record_progress(db, payload)
    return record


@router.get("/{user_id}", response_model=ProgressDashboard)
async def progress_dashboard(user_id: str, db: AsyncSession = Depends(get_db)):
    """Get the progress dashboard for a user."""
    return await get_dashboard(db, user_id)
