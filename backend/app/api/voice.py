"""Voice interaction API routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.services.voice import speak, listen, parse_voice_command
from backend.app.services.notifications import send_notification, get_notifications, mark_read

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SpeakRequest(BaseModel):
    text: str
    rate: int = 160


class VoiceCommandResponse(BaseModel):
    raw_text: str
    command: str


@router.post("/speak")
async def tts(payload: SpeakRequest):
    """Convert text to speech."""
    result = speak(payload.text, payload.rate)
    return result


@router.post("/listen")
async def stt():
    """Listen to microphone and return recognised text."""
    result = listen()
    return result


@router.post("/command")
async def process_voice_command(text: str):
    """Parse a text command (from STT or typed) into an app action."""
    parsed = parse_voice_command(text)
    return parsed


# ── Notifications ───────────────────────────────────────────────────

class NotificationRequest(BaseModel):
    user_id: str
    title: str
    body: str
    channel: str = "app"


@router.post("/notify")
async def notify(payload: NotificationRequest):
    """Send a notification to a user."""
    return send_notification(payload.user_id, payload.title, payload.body, payload.channel)


@router.get("/notifications/{user_id}")
async def list_notifications(user_id: str, unread_only: bool = False):
    """List notifications for a user."""
    return get_notifications(user_id, unread_only)


@router.post("/notifications/{user_id}/read")
async def read_notifications(user_id: str):
    """Mark all notifications as read."""
    count = mark_read(user_id)
    return {"marked_read": count}
