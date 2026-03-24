"""Educational chatbot API."""

from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models.chat import ChatMessage as ChatMessageModel
from backend.app.models.user import User
from backend.app.schemas.chat import ChatAskRequest, ChatAskResponse, ChatHistoryItem, ChatMessage

router = APIRouter(prefix="/api/chat", tags=["chat"])

NON_EDUCATIONAL_TERMS = {
    "trip",
    "travel",
    "tour",
    "tourism",
    "vacation",
    "holiday",
    "hotel",
    "flight",
    "restaurant",
    "food",
    "curry",
    "recipe",
    "movie",
    "song",
    "lyrics",
    "celebrity",
    "actor",
    "actress",
    "cricket",
    "football",
    "ipl",
    "score",
    "weather",
    "bitcoin",
    "crypto",
    "stock",
    "price",
    "shopping",
    "buy",
    "amazon",
    "flipkart",
}

BLOCKED_TOPIC_LABELS = [
    "travel and trip planning",
    "weather",
    "sports scores",
    "shopping and product buying",
    "stock or crypto prices",
    "movies, songs, and celebrities",
]


def _collapse_repeated_lines(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    compact = [line for line in lines if line]
    deduped: list[str] = []
    for line in compact:
        if not deduped or deduped[-1].casefold() != line.casefold():
            deduped.append(line)
    return " ".join(deduped) if deduped else (text or "").strip()


async def _log_chat_message(
    db: AsyncSession,
    user_id: str,
    role: str,
    content: str,
    mode: str | None = None,
) -> None:
    if not user_id or not content:
        return
    try:
        db.add(
            ChatMessageModel(
                user_id=user_id,
                role=role,
                content=content[:4000],
                mode=mode[:50] if mode else None,
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=404, detail="User not found. Please sign in again.")


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _is_non_educational_query(message: str) -> bool:
    normalized = f" {_normalize(message)} "
    return any(f" {term} " in normalized for term in NON_EDUCATIONAL_TERMS)


def _non_educational_redirect() -> tuple[str, list[str], list[str]]:
    blocked = ", ".join(BLOCKED_TOPIC_LABELS)
    return (
        f"I am restricted to educational help only. I do not answer {blocked}. Ask about concepts, programming, maths, science, study methods, or exam preparation.",
        ["education_only"],
        [],
    )


async def _llm_answer(message: str, history: list[ChatMessage]) -> tuple[str | None, str | None]:
    get_settings.cache_clear()
    settings = get_settings()
    provider = (settings.ai_provider or "").lower()

    if provider != "groq" or not settings.groq_api_key:
        return None, None

    messages = [
        {
            "role": "system",
            "content": (
                "You are Study Assistant AI, a specialized educational assistant. "
                "You must only answer educational questions about learning, academics, concepts, problem solving, exam preparation, study methods, and programming. "
                "If the user asks for something outside education, reply briefly that you are focused on educational help only."
            ),
        },
    ]
    for item in history[-10:]:
        messages.append({"role": item.role, "content": item.content})
    messages.append({"role": "user", "content": message})

    transport = (
        httpx.AsyncHTTPTransport(proxy=settings.groq_proxy_url)
        if getattr(settings, "groq_proxy_url", None)
        else None
    )

    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            trust_env=not bool(settings.groq_proxy_url),
            transport=transport,
        ) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.groq_model,
                    "messages": messages,
                    "temperature": 0.4,
                },
            )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip(), "groq"
    except Exception:
        return None, "groq_fallback"


@router.get("/history/{user_id}", response_model=list[ChatHistoryItem])
async def chat_history(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChatMessageModel)
        .where(ChatMessageModel.user_id == user_id)
        .order_by(ChatMessageModel.created_at.desc())
        .limit(100)
    )
    messages = result.scalars().all()
    return [ChatHistoryItem.from_orm(msg) for msg in reversed(messages)]


@router.post("/ask", response_model=ChatAskResponse)
async def ask_chatbot(payload: ChatAskRequest, db: AsyncSession = Depends(get_db)):
    user_exists = await db.scalar(select(User.id).where(User.id == payload.user_id))
    if not user_exists:
        raise HTTPException(status_code=404, detail="User not found. Please sign in again.")

    await _log_chat_message(db, payload.user_id, "user", payload.message, "user_query")

    if _is_non_educational_query(payload.message):
        answer, sources, suggestions = _non_educational_redirect()
        await _log_chat_message(db, payload.user_id, "assistant", answer, "education_only")
        return ChatAskResponse(
            answer=answer,
            sources=sources,
            suggestions=suggestions,
            mode="education_only",
        )

    ai_answer, provider = await _llm_answer(payload.message, payload.history)
    if ai_answer:
        await _log_chat_message(db, payload.user_id, "assistant", ai_answer, provider or "groq")
        return ChatAskResponse(answer=ai_answer, sources=["groq"], suggestions=[], mode=provider or "groq")

    answer = "I could not get a live Groq answer right now. Please try again in a moment."
    fallback_mode = provider or "groq_fallback"
    await _log_chat_message(db, payload.user_id, "assistant", answer, fallback_mode)
    return ChatAskResponse(answer=answer, sources=[], suggestions=[], mode=fallback_mode)
