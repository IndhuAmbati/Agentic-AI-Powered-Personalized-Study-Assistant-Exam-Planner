"""
Voice Interaction Service
=========================
Provides text-to-speech (TTS) and speech-to-text (STT) capabilities.

* TTS: ``pyttsx3`` for offline synthesis.
* STT: ``SpeechRecognition`` library with Google Web Speech API.

Both are wrapped behind a simple interface so they can be swapped
for cloud providers (Google Cloud TTS, Whisper, etc.) easily.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Text-to-Speech ──────────────────────────────────────────────────

def speak(text: str, rate: int = 160) -> dict[str, Any]:
    """
    Convert *text* to speech using pyttsx3.

    Returns a status dict. Actual audio playback happens on the server
    (useful for local/demo mode). For web deployment, return audio bytes
    via a streaming endpoint instead.
    """
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", rate)
        engine.say(text)
        engine.runAndWait()
        return {"status": "ok", "text": text}
    except Exception as exc:
        logger.warning("TTS failed: %s", exc)
        return {"status": "error", "detail": str(exc), "text": text}


# ── Speech-to-Text ──────────────────────────────────────────────────

def listen(timeout: int = 5, phrase_time_limit: int = 10) -> dict[str, Any]:
    """
    Listen to microphone input and return recognised text.

    Uses ``speech_recognition`` with Google Web Speech API (free tier).
    """
    try:
        import speech_recognition as sr

        recogniser = sr.Recognizer()
        with sr.Microphone() as source:
            recogniser.adjust_for_ambient_noise(source, duration=0.5)
            logger.info("Listening …")
            audio = recogniser.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)

        text = recogniser.recognize_google(audio)
        return {"status": "ok", "text": text}
    except Exception as exc:
        logger.warning("STT failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ── Command Parser ──────────────────────────────────────────────────

COMMAND_MAP: dict[str, str] = {
    "generate schedule": "schedule_generate",
    "show schedule": "schedule_view",
    "show progress": "progress_view",
    "start quiz": "quiz_start",
    "adapt plan": "agent_adapt",
    "help": "help",
}


def parse_voice_command(text: str) -> dict[str, Any]:
    """
    Map raw speech text to an application command.

    Returns ``{"command": "<action>", "raw": "<original_text>"}``
    or ``{"command": "unknown", ...}`` if no match found.
    """
    normalised = text.strip().lower()
    for phrase, action in COMMAND_MAP.items():
        if phrase in normalised:
            return {"command": action, "raw": text}
    return {"command": "unknown", "raw": text}
