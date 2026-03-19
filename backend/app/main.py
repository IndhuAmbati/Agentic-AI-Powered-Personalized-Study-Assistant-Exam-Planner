"""
FastAPI Application Entry Point
================================
Wires together all routers and starts up the database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.database import init_db

# Import routers
from backend.app.api.users import router as users_router
from backend.app.api.subjects import router as subjects_router
from backend.app.api.topics import router as topics_router
from backend.app.api.schedule import router as schedule_router
from backend.app.api.progress import router as progress_router
from backend.app.api.quiz import router as quiz_router
from backend.app.api.agent import router as agent_router
from backend.app.api.voice import router as voice_router
from backend.app.api.auth import router as auth_router
from backend.app.api.syllabus import router as syllabus_router
from backend.app.api.chat import router as chat_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # Startup: create tables
    await init_db()
    yield
    # Shutdown: nothing special needed for SQLite


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Agentic AI-Powered Personalized Study Assistant & Exam Planner",
    lifespan=lifespan,
)

# CORS — allow Streamlit and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(users_router)
app.include_router(subjects_router)
app.include_router(topics_router)
app.include_router(schedule_router)
app.include_router(progress_router)
app.include_router(quiz_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(auth_router)
app.include_router(syllabus_router)
app.include_router(chat_router)


@app.get("/", tags=["health"])
async def root():
    """Health-check endpoint."""
    return {"status": "ok", "app": settings.app_name, "version": "1.0.0"}


@app.get("/api/health", tags=["health"])
async def health():
    """Detailed health check."""
    return {
        "status": "healthy",
        "database": settings.database_url.split("///")[0],
        "ai_provider": settings.ai_provider,
        "voice_enabled": settings.voice_enabled,
    }
