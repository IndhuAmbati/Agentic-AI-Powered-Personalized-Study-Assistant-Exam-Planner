"""
Quiz Generation & Evaluation Engine
====================================
Generates level-wise quizzes for topics and evaluates student responses.

For the rule-based provider the engine uses a template-based approach with
pre-defined question banks. When an LLM provider is configured it can
delegate to an external API.
"""

from __future__ import annotations

import json
import asyncio
import logging
import random
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.config import get_settings
from backend.app.models.quiz import Quiz, QuizQuestion, QuizAttempt
from backend.app.models.quiz_performance import QuizPerformance
from backend.app.models.topic import Topic
from backend.app.models.subject import Subject
from backend.app.models.progress import ProgressRecord
from backend.app.models.schedule import ScheduleEntry
from backend.app.services.adaptive_agent import AdaptiveAgent
from backend.app.schemas.quiz import (
    QuizGenerateRequest,
    QuizSubmitRequest,
    QuizResult,
)


_UNIT_PREFIX_RE = re.compile(r"^\s*(Unit\s+[IVXLC\d]+)\s*:\s*(.+)$", re.IGNORECASE)
_PART_SPLIT_RE = re.compile(r"\s*(?:,|;|\||/|&|\band\b)\s*", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
def _fallback_distractors(core: str) -> list[str]:
    """Construct topic-aware distractors instead of generic 'unrelated' fillers."""
    base = _normalize_phrase(core) or "the topic"
    lower = base.lower()
    domain_distractors: list[str] = []

    if any(
        keyword in lower
        for keyword in [
            "sensor",
            "sensors",
            "signal",
            "signals",
            "gps",
            "telemetry",
            "iot",
            "device data",
        ]
    ):
        domain_distractors = [
            "Ignoring calibration and unit consistency between sensors",
            "Sampling at an unsuitable rate causing aliasing",
            "Failing to filter noise or denoise raw signals",
            "Not synchronizing timestamps across sensor streams",
            "Dropping packets without handling missing values",
        ]
    elif any(keyword in lower for keyword in ["network", "communication", "protocol", "m2m", "machine to machine"]):
        domain_distractors = [
            "Ignoring authentication between devices",
            "Using incompatible protocols between endpoints",
            "Neglecting latency and QoS requirements",
            "Skipping encryption for telemetry data",
            "Hard-coding device credentials",
        ]
    elif any(keyword in lower for keyword in ["database", "sql", "warehouse", "olap", "oltp", "data ", "data-"]):
        domain_distractors = [
            "Skipping normalization and indexing",
            "Querying without filtering or limits",
            "Ignoring transaction isolation",
            "Using OLAP patterns on OLTP engines",
        ]
    elif any(keyword in lower for keyword in ["ml", "model", "learning", "ai"]):
        domain_distractors = [
            "Training without a validation split",
            "Leaking labels into features",
            "Ignoring class imbalance",
            "Deploying without monitoring drift",
        ]

    if not domain_distractors:
        domain_distractors = [
            f"Confusing {base} with a different concept",
            f"Applying {base} without prerequisites",
            f"Using {base} in an unrelated context",
            f"Skipping key steps when learning {base}",
            f"Memorizing {base} without practice",
        ]

    return domain_distractors

PASS_THRESHOLDS = {
    "easy": 75.0,
    "medium": 75.0,
    "hard": 75.0,
}
logger = logging.getLogger(__name__)
settings = get_settings()

# Keep outbound LLM calls fast so the UI never times out.
_LLM_TIMEOUT = httpx.Timeout(connect=4.0, read=6.0, write=6.0, pool=None)
_QUIZ_BATCH_TIMEOUT_SECS = 10.0


def _strip_unit_prefix(topic_name: str) -> tuple[str, str | None]:
    match = _UNIT_PREFIX_RE.match(topic_name or "")
    if not match:
        return (str(topic_name or "").strip(), None)
    return (match.group(2).strip(), match.group(1).strip())


def _normalize_phrase(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    return cleaned.strip(" .,-:")


def _normalize_question_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _groq_max_tokens_for_quiz(count: int) -> int:
    """Scale the completion budget with requested question count to avoid truncation."""
    safe_count = max(1, int(count))
    return min(1400, max(450, 110 * safe_count + 80))


def _groq_models_to_try() -> list[str]:
    primary = str(getattr(settings, "groq_model", "") or "").strip()
    fallback_raw = str(getattr(settings, "groq_fallback_models", "") or "").strip()
    candidates = [primary] if primary else []
    if fallback_raw:
        candidates.extend(
            model.strip() for model in fallback_raw.split(",") if model.strip()
        )
    seen: set[str] = set()
    deduped: list[str] = []
    for model in candidates:
        key = model.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(model)
    return deduped


def _extract_retry_hint(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"try again in ([0-9a-zA-Z\s:]+?)(?:[.,]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(".")
    return None


def _extract_json_payload(content: str) -> Any:
    """Parse JSON from strict output, fenced code blocks, or text-wrapped payloads."""
    if not content:
        raise ValueError("Groq response missing content")

    decoder = json.JSONDecoder()
    candidates: list[str] = []
    seen: set[str] = set()

    def _add_candidate(candidate: str) -> None:
        text = candidate.strip()
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)

    _add_candidate(content)
    for match in _CODE_FENCE_RE.finditer(content):
        _add_candidate(match.group(1))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        for idx, char in enumerate(candidate):
            if char not in "[{":
                continue
            snippet = candidate[idx:].strip()
            try:
                parsed, _ = decoder.raw_decode(snippet)
                return parsed
            except json.JSONDecodeError:
                continue

    raise ValueError("Groq response JSON parse failed")


def _question_lead_signature(text: str) -> str:
    normalized = _normalize_question_text(text)
    words = [word for word in re.findall(r"[a-z0-9]+", normalized) if word]
    if not words:
        return ""
    return " ".join(words[:5])


def _has_bad_option(*options: str) -> bool:
    normalized_options = [_normalize_phrase(option) for option in options]
    if len(normalized_options) != 4:
        return True

    seen: set[str] = set()
    for option in normalized_options:
        if not option or len(option) < 2:
            return True
        lowered = option.lower()
        if lowered.startswith("option "):
            return True
        key = re.sub(r"[^a-z0-9]+", "", lowered)
        if not key or key in seen:
            return True
        seen.add(key)
    return False


def _is_generic_textbook_stem(
    qtext: str, topic_name: str, subject_name: str | None = None
) -> bool:
    normalized = _normalize_question_text(qtext)
    if not normalized:
        return True

    generic_patterns = [
        "primary goal of",
        "purpose of",
        "common assumption of",
        "common misconception about",
        "which of the following is a common assumption",
        "what is the primary goal",
        "what is the purpose",
        "a common misconception",
        "which of the following is a common",
    ]
    if any(pattern in normalized for pattern in generic_patterns):
        topic_tokens = _topic_keywords(topic_name, subject_name)
        if not topic_tokens or not any(token in normalized for token in topic_tokens):
            return True

    meta_patterns = [
        "according to the syllabus",
        "in this unit",
        "the chapter discusses",
        "learning outcome",
        "study tip",
        "revision strategy",
    ]
    return any(pattern in normalized for pattern in meta_patterns)


def _topic_keywords(topic_name: str, subject_name: str | None = None) -> set[str]:
    stop = {
        "the",
        "a",
        "an",
        "of",
        "and",
        "to",
        "in",
        "for",
        "on",
        "by",
        "with",
        "from",
        "using",
        "unit",
        "chapter",
        "topic",
        "introduction",
        "basics",
        "overview",
    }
    tokens: set[str] = set()
    for source in [topic_name, subject_name]:
        if not source:
            continue
        cleaned = re.sub(r"[^a-z0-9]+", " ", source.lower())
        for token in cleaned.split():
            if len(token) < 4 or token in stop:
                continue
            tokens.add(token)
    return tokens




def _is_question_on_topic(
    qtext: str, topic_name: str, subject_name: str | None = None
) -> bool:
    """Keep questions grounded to the target topic/subject without being over-strict."""
    if not qtext or not topic_name:
        return False
    normalized_q = _normalize_question_text(qtext)
    parts = _split_topic_parts(topic_name)
    if not parts:
        return True  # nothing to check against, allow
    for part in parts:
        key = _normalize_question_text(part)
        if not key or len(key) < 4:
            continue
        if key in normalized_q:
            return True
    topic_tokens = _topic_keywords(topic_name, subject_name)
    if topic_tokens:
        overlap = sum(1 for token in topic_tokens if token in normalized_q)
        if overlap >= min(2, len(topic_tokens)):
            return True
        # For narrow technical topics, one strong keyword hit is often enough.
        if overlap >= 1 and len(topic_tokens) <= 3:
            return True
    if subject_name:
        subject_parts = _split_topic_parts(subject_name)
        for part in subject_parts or [subject_name]:
            subject_key = _normalize_question_text(part)
            if subject_key and len(subject_key) >= 4 and subject_key in normalized_q:
                return True
        subject_tokens = _topic_keywords(subject_name)
        if subject_tokens and any(token in normalized_q for token in subject_tokens):
            return True
    return False
def _is_low_quality_question(qtext: str) -> bool:
    """Heuristic filter to drop trivial or meta questions."""
    if not qtext:
        return True
    normalized = _normalize_question_text(qtext)
    # Allow slightly shorter LLM questions to avoid over-filtering
    if len(normalized) < 24:
        return True
    
    
    return False


def _split_topic_parts(topic_name: str) -> list[str]:
    body, _ = _strip_unit_prefix(topic_name)
    parts: list[str] = []
    for candidate in _PART_SPLIT_RE.split(body):
        phrase = _normalize_phrase(candidate)
        if not phrase:
            continue
        if len(phrase.split()) < 2:
            continue
        parts.append(phrase)
    if not parts and body:
        parts = [body]
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        key = re.sub(r"[^a-z0-9]+", "", part.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return deduped


def _filter_related_by_overlap(topic_parts: list[str], related_candidates: list[str]) -> list[str]:
    """Keep related topics that have meaningful token overlap (ignores generic words)."""
    stop = {"the", "a", "an", "of", "and", "to", "in", "for", "on", "introduction"}
    topic_tokens = set()
    for part in topic_parts:
        topic_tokens.update(
            t for t in re.sub(r"[^a-z0-9]+", " ", part.lower()).split() if t not in stop
        )
    filtered: list[str] = []
    for cand in related_candidates:
        cand_tokens = set(
            t for t in re.sub(r"[^a-z0-9]+", " ", cand.lower()).split() if t not in stop
        )
        overlap = topic_tokens & cand_tokens
        if not topic_tokens or not overlap:
            continue
        filtered.append(cand)
    return filtered


def _build_options(correct: str, distractors: list[str]) -> tuple[dict[str, str], str]:
    unique_distractors: list[str] = []
    seen = {re.sub(r"[^a-z0-9]+", "", correct.lower())}
    for item in distractors:
        text = _normalize_phrase(item)
        if not text:
            continue
        key = re.sub(r"[^a-z0-9]+", "", text.lower())
        if key in seen:
            continue
        seen.add(key)
        unique_distractors.append(text)
    if len(unique_distractors) < 3:
        for fallback in _fallback_distractors(correct):
            if len(unique_distractors) >= 3:
                break
            key = re.sub(r"[^a-z0-9]+", "", fallback.lower())
            if key not in seen:
                seen.add(key)
                unique_distractors.append(fallback)

    options = [correct] + unique_distractors[:3]
    random.shuffle(options)
    labels = ["A", "B", "C", "D"]
    option_map = {labels[idx]: options[idx] for idx in range(4)}
    correct_letter = next(label for label, value in option_map.items() if value == correct)
    return option_map, correct_letter


def _topic_grounded_mcq(
    *,
    stem: str,
    correct: str,
    distractors: list[str],
    explanation: str,
    order_index: int,
) -> dict[str, Any]:
    option_map, answer = _build_options(correct, distractors)
    return {
        "question_text": stem,
        "option_a": option_map["A"],
        "option_b": option_map["B"],
        "option_c": option_map["C"],
        "option_d": option_map["D"],
        "correct_answer": answer,
        "explanation": explanation,
        "order_index": order_index,
    }


def _generate_questions(topic_name: str, difficulty: str, count: int) -> list[dict]:
    """Static quiz generation is disabled; quizzes must come from Groq."""
    return []


def _generate_contextual_rule_based_questions(
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None = None,
) -> list[dict]:
    """Static quiz generation is disabled; quizzes must come from Groq."""
    return []


def _filter_and_backfill_questions(
    *,
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None,
    questions: list[dict],
    allow_rule_backfill: bool = True,
    subject_name: str | None = None,
) -> list[dict]:
    """Remove low-quality/off-topic/duplicate questions without static backfill."""
    filtered: list[dict] = []
    used = used_questions if used_questions is not None else set()
    seen: set[str] = set()
    seen_leads: set[str] = set()

    for q in questions:
        qtext = q.get("question_text", "")
        if _is_low_quality_question(qtext):
            continue
        if not _is_question_on_topic(qtext, topic_name, subject_name):
            continue
        if _is_generic_textbook_stem(qtext, topic_name, subject_name):
            continue
        key = _normalize_question_text(qtext)
        lead_key = _question_lead_signature(qtext)
        if key in seen or key in used:
            continue
        if lead_key and lead_key in seen_leads:
            continue
        seen.add(key)
        if lead_key:
            seen_leads.add(lead_key)
        used.add(key)
        q = dict(q)
        q["order_index"] = len(filtered)
        filtered.append(q)
        if len(filtered) >= count:
            return filtered

    def _take_raw_groq_items(raw_items: list[dict]) -> list[dict]:
        """Deduplicate and cap raw Groq items when strict filtering drops too many."""
        cleaned: list[dict] = []
        seen_keys: set[str] = set()
        for item in raw_items:
            if len(cleaned) >= count:
                break
            qtext = str(item.get("question_text", "")).strip()
            if not qtext:
                continue
            key = _normalize_question_text(qtext)
            if key in seen_keys or key in used:
                continue
            if _is_low_quality_question(qtext):
                continue
            if _is_generic_textbook_stem(qtext, topic_name, subject_name):
                continue
            opts = (
                str(item.get("option_a", "")),
                str(item.get("option_b", "")),
                str(item.get("option_c", "")),
                str(item.get("option_d", "")),
            )
            if _has_bad_option(*opts):
                continue
            seen_keys.add(key)
            used.add(key)
            ans = str(item.get("correct_answer", "A")).upper()
            if ans not in {"A", "B", "C", "D"}:
                ans = "A"
            cleaned.append(
                {
                    "question_text": qtext,
                    "option_a": opts[0],
                    "option_b": opts[1],
                    "option_c": opts[2],
                    "option_d": opts[3],
                    "correct_answer": ans,
                    "explanation": str(item.get("explanation", "")),
                    "order_index": len(cleaned),
                }
            )
        return cleaned

    if len(filtered) < count:
        if getattr(settings, "require_groq", False) and questions:
            # Groq-only mode: after filtering, fall back to deduped raw Groq items
            filtered.extend(_take_raw_groq_items(questions))

    return filtered


async def _generate_questions_with_llm(
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None = None,
    subject_name: str | None = None,
) -> list[dict]:
    """Generate quiz items using the configured provider."""
    provider = (settings.ai_provider or "groq").lower()
    if provider != "groq":
        raise RuntimeError("Quiz generation is Groq-only. Set AI_PROVIDER=groq.")
    if not settings.groq_api_key:
        raise RuntimeError("Groq API key is missing")
    allow_fallback = getattr(settings, "allow_groq_fallback", False)
    try:
        return await _generate_questions_with_groq(
            topic_name, difficulty, count, related_topics, used_questions, subject_name
        )
    except Exception as exc:
        if allow_fallback:
            logger.warning(
                "Groq failed (%s); falling back to rule-based quiz generation", exc
            )
            return _generate_contextual_rule_based_questions(
                topic_name, difficulty, count, related_topics, used_questions
            )
        raise


async def _generate_questions_with_openai(
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None = None,
    subject_name: str | None = None,
) -> list[dict]:
    system_prompt = (
        "You write high-quality, topic-grounded MCQs for exam prep. "
        "Each question must test actual understanding of the given topic. "
        "Never ask about units/chapters, learning outcomes, study strategies, or revision steps; avoid meta or syllabus trivia. "
        "Include plausible but incorrect distractors. Avoid placeholders like 'Option A'. Return strict JSON only."
    )
    related = ", ".join(related_topics[:6]) if related_topics else "None"
    subject_clause = (
        f"The subject is '{subject_name}'. Keep every question within this subject and the topic."
        if subject_name
        else ""
    )
    user_prompt = (
        f"Generate {count} {difficulty} multiple-choice questions for the topic '{topic_name}'. "
        f"Related syllabus topics to reference for plausible distractors: {related}. "
        f"{subject_clause} "
        "Mix question styles: definition/recall, application, and a common misconception check. "
        "Each item JSON keys: question_text, option_a, option_b, option_c, option_d, correct_answer (A/B/C/D), explanation. "
        "Keep questions concise, on-topic, and meaningful."
    )

    try:
        transport = (
            httpx.AsyncHTTPTransport(proxy=settings.openai_proxy_url)
            if settings.openai_proxy_url
            else None
        )
        # Ignore env proxies; use explicit proxy only if provided
        async with httpx.AsyncClient(
            timeout=_LLM_TIMEOUT,
            trust_env=False,
            transport=transport,
        ) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0.4,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError("OpenAI quiz generation failed") from exc

    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        raise ValueError("OpenAI response missing JSON array of questions")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise ValueError("OpenAI response JSON parse failed")

    formatted: list[dict] = []
    seen_questions: set[str] = set()
    used = used_questions or set()
    for idx, item in enumerate(parsed):
        answer = str(item.get("correct_answer", "A")).upper()
        if answer not in {"A", "B", "C", "D"}:
            answer = "A"
        qtext = str(item.get("question_text", f"Question on {topic_name}")).strip()
        if _is_low_quality_question(qtext):
            continue
        if not _is_question_on_topic(qtext, topic_name, subject_name):
            continue
        if _is_generic_textbook_stem(qtext, topic_name, subject_name):
            continue
        opts = (
            str(item.get("option_a", "Option A")),
            str(item.get("option_b", "Option B")),
            str(item.get("option_c", "Option C")),
            str(item.get("option_d", "Option D")),
        )
        if _has_bad_option(*opts):
            continue
        key = _normalize_question_text(qtext)
        if key in seen_questions or key in used:
            continue
        seen_questions.add(key)
        used.add(key)
        formatted.append(
            {
                "question_text": qtext,
                "option_a": opts[0],
                "option_b": opts[1],
                "option_c": opts[2],
                "option_d": opts[3],
                "correct_answer": answer,
                "explanation": str(item.get("explanation", "")),
                "order_index": len(formatted),
            }
        )
        if len(formatted) >= count:
            break

    return formatted


async def _generate_questions_with_groq(
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None = None,
    subject_name: str | None = None,
) -> list[dict]:
    """Generate questions through Groq's OpenAI-compatible chat completions API."""
    logger.info(
        "Groq quiz generation start topic=%s difficulty=%s count=%d",
        topic_name,
        difficulty,
        count,
    )
    system_prompt = (
        "You are an expert exam-question writer. "
        "Write concise, exam-ready MCQs that stay tightly inside the given subject and topic. "
        "Avoid generic or cross-domain content; every stem and option must clearly reference knowledge from this topic/subject. "
        "Mix question styles: (1) key concept/definition, (2) applied scenario, (3) misconception check. "
        "Do NOT repeat the topic name verbatim as the correct answer. "
        "All options must be plausible and mutually exclusive; avoid placeholders like 'Option A/B' or obviously wrong fillers. "
        "Keep wording specific, technical when appropriate, and free of meta text (no study tips, unit names, or syllabus chatter). "
        "Return JSON only."
    )
    related = ", ".join(related_topics[:6]) if related_topics else "None"
    subject_clause = (
        f"The subject is '{subject_name}'. Keep every question inside this subject and the topic; avoid off-subject content."
        if subject_name
        else ""
    )
    user_prompt = (
        f"Generate {count} {difficulty} multiple-choice questions for the topic '{topic_name}'. "
        f"Related syllabus topics to draw plausible distractors from: {related}. "
        f"{subject_clause} "
        "Requirements:\n"
        "- Include at least one applied scenario and one misconception-detection question.\n"
        "- Avoid generic IoT/CS/business examples unless they genuinely belong to this topic.\n"
        "- Vary stems; no two stems should start the same way; avoid echoing the topic wording.\n"
        "- Each option must be short (<=18 words) and not a rephrasing of another option.\n"
        "- Correct answer should be non-obvious yet unambiguously correct.\n"
        "- Do not wrap the JSON in markdown fences or add any prose before/after it.\n"
        "Return JSON array where each item has: question_text, option_a, option_b, option_c, option_d, "
        "correct_answer (A/B/C/D), explanation."
    )

    def _extract_questions_from_content(content: str) -> list[dict]:
        try:
            parsed = _extract_json_payload(content)
        except ValueError:
            if not re.search(r"\[", content):
                logger.warning("Groq content missing JSON array")
                raise ValueError("Groq response missing JSON array of questions")
            logger.warning("Groq JSON parse failed")
            raise
        if isinstance(parsed, dict):
            for key in ("questions", "items", "data"):
                nested = parsed.get(key)
                if isinstance(nested, list):
                    return nested
            raise ValueError("Groq response JSON missing question array")
        if not isinstance(parsed, list):
            raise ValueError("Groq response JSON did not contain an array")
        return parsed

    parsed_questions: list[dict] | None = None
    models_to_try = _groq_models_to_try()
    MAX_ATTEMPTS = 2
    try:
        transport = (
            httpx.AsyncHTTPTransport(proxy=settings.groq_proxy_url)
            if settings.groq_proxy_url
            else None
        )
        # Ignore env proxies; use explicit proxy only if provided
        async with httpx.AsyncClient(
            timeout=_LLM_TIMEOUT,
            # Allow corporate/system proxies when no explicit proxy is supplied
            trust_env=not bool(settings.groq_proxy_url),
            transport=transport,
        ) as client:
            resp = None
            for model_name in models_to_try:
                for attempt in range(MAX_ATTEMPTS):
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.groq_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model_name,
                            "temperature": 0.4,
                            "max_tokens": _groq_max_tokens_for_quiz(count),
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                        },
                    )
                    if resp.status_code == 429:
                        retry_hint = _extract_retry_hint(resp.text or "")
                        logger.warning(
                            "Groq rate limit for model=%s attempt=%d detail=%s",
                            model_name,
                            attempt + 1,
                            resp.text,
                        )
                        if model_name != models_to_try[-1]:
                            logger.info(
                                "Trying fallback Groq model after rate limit on %s",
                                model_name,
                            )
                            break
                        if attempt == MAX_ATTEMPTS - 1:
                            detail = f"Groq rate limit reached for model '{model_name}'"
                            if retry_hint:
                                detail = f"{detail}. Try again in {retry_hint}."
                            raise RuntimeError(detail)
                        retry_after = float(resp.headers.get("retry-after", "1.5"))
                        wait = min(3.0, max(retry_after, 1.5 * (attempt + 1)))
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code >= 500:
                        if attempt < MAX_ATTEMPTS - 1:
                            logger.warning(
                                "Groq transient error model=%s status=%s retrying: %s",
                                model_name,
                                resp.status_code,
                                resp.text,
                            )
                            await asyncio.sleep(min(2.0, 1.0 * (attempt + 1)))
                            continue
                        break

                    resp.raise_for_status()
                    data = resp.json()
                    if not data.get("choices"):
                        logger.error("Groq response missing choices: %s", data)
                        raise ValueError("Groq response missing choices array")
                    content = data["choices"][0]["message"]["content"]
                    logger.info(
                        "Groq response received model=%s length=%d",
                        model_name,
                        len(content or ""),
                    )
                    try:
                        parsed_questions = _extract_questions_from_content(content)
                        break
                    except ValueError as parse_exc:
                        logger.warning(
                            "Groq parse attempt %d failed for topic=%s model=%s: %s",
                            attempt + 1,
                            topic_name,
                            model_name,
                            parse_exc,
                        )
                        if attempt == MAX_ATTEMPTS - 1:
                            raise
                        await asyncio.sleep(0.5)
                        continue
                if parsed_questions is not None:
                    break

            if resp is None:
                raise RuntimeError("Groq did not return a response")
            if parsed_questions is None:
                raise RuntimeError("Groq did not return a parsable response")
    except Exception as exc:
        body = None
        status_code = None
        try:
            body = resp.text  # type: ignore[name-defined]
        except Exception:
            pass
        try:
            status_code = resp.status_code  # type: ignore[name-defined]
        except Exception:
            pass
        logger.exception("Groq quiz generation failed. Response body: %s", body)
        detail = "Groq quiz generation failed"
        if status_code:
            detail = f"{detail} (HTTP {status_code})"
        if body:
            compact_body = re.sub(r"\s+", " ", body).strip()
            detail = f"{detail}: {compact_body[:240]}"
        raise RuntimeError(detail) from exc

    formatted: list[dict] = []
    seen_questions: set[str] = set()
    used = used_questions or set()
    reject_counts = {
        "low_quality": 0,
        "off_topic": 0,
        "generic": 0,
        "bad_options": 0,
        "duplicate": 0,
    }
    for idx, item in enumerate(parsed_questions):
        answer = str(item.get("correct_answer", "A")).upper()
        if answer not in {"A", "B", "C", "D"}:
            answer = "A"
        qtext = str(item.get("question_text", f"Question on {topic_name}")).strip()
        if _is_low_quality_question(qtext):
            reject_counts["low_quality"] += 1
            continue
        if not _is_question_on_topic(qtext, topic_name, subject_name):
            reject_counts["off_topic"] += 1
            continue
        if _is_generic_textbook_stem(qtext, topic_name, subject_name):
            reject_counts["generic"] += 1
            continue
        opts = (
            str(item.get("option_a", "Option A")),
            str(item.get("option_b", "Option B")),
            str(item.get("option_c", "Option C")),
            str(item.get("option_d", "Option D")),
        )
        if _has_bad_option(*opts):
            reject_counts["bad_options"] += 1
            continue
        key = _normalize_question_text(qtext)
        if key in seen_questions or key in used:
            reject_counts["duplicate"] += 1
            continue
        seen_questions.add(key)
        used.add(key)
        formatted.append(
            {
                "question_text": qtext,
                "option_a": opts[0],
                "option_b": opts[1],
                "option_c": opts[2],
                "option_d": opts[3],
                "correct_answer": answer,
                "explanation": str(item.get("explanation", "")),
                "order_index": len(formatted),
            }
        )
        if len(formatted) >= count:
            break

    logger.info(
        "Groq quiz filter results topic=%s accepted=%d parsed=%d rejects=%s",
        topic_name,
        len(formatted),
        len(parsed_questions),
        reject_counts,
    )

    if len(formatted) < count:
        # Prefer salvaging more Groq items (even if they failed earlier filters) before rule-based fill
        for item in parsed_questions:
            if len(formatted) >= count:
                break
            qtext = str(item.get("question_text", "")).strip()
            key = _normalize_question_text(qtext)
            if key in seen_questions or key in used:
                continue
            answer = str(item.get("correct_answer", "A")).upper()
            opts = (
                str(item.get("option_a", "Option A")),
                str(item.get("option_b", "Option B")),
                str(item.get("option_c", "Option C")),
                str(item.get("option_d", "Option D")),
            )
            # Skip salvage items that still look like placeholders
            if not qtext or any(opt.lower().startswith("option") for opt in opts):
                continue
            if _is_low_quality_question(qtext):
                continue
            if _is_generic_textbook_stem(qtext, topic_name, subject_name):
                continue
            if _has_bad_option(*opts):
                continue
            formatted.append(
                {
                    "question_text": qtext or f"Question on {topic_name}",
                    "option_a": opts[0],
                    "option_b": opts[1],
                    "option_c": opts[2],
                    "option_d": opts[3],
                    "correct_answer": answer if answer in {"A", "B", "C", "D"} else "A",
                    "explanation": str(item.get("explanation", "")),
                    "order_index": len(formatted),
                }
            )

    return formatted


async def _create_review_session(
    db: AsyncSession,
    *,
    user_id: str,
    topic: Topic | None,
) -> bool:
    """Create a single revision schedule block on the next available day."""
    if not topic:
        return False

    subject = await db.get(Subject, topic.subject_id)
    subject_name = subject.name if subject else "General"
    base_start = time(19, 0)
    duration_mins = 60.0
    start_day = date.today() + timedelta(days=1)

    for day_offset in range(0, 14):
        proposed_day = start_day + timedelta(days=day_offset)
        start_dt = datetime.combine(proposed_day, base_start)
        end_dt = start_dt + timedelta(minutes=duration_mins)
        proposed_start = start_dt.time()
        proposed_end = end_dt.time()

        existing_q = (
            select(ScheduleEntry)
            .where(
                ScheduleEntry.user_id == user_id,
                ScheduleEntry.scheduled_date == proposed_day,
            )
            .order_by(ScheduleEntry.start_time)
        )
        existing_result = await db.execute(existing_q)
        existing = list(existing_result.scalars().all())

        overlaps = any(
            proposed_start < e.end_time and proposed_end > e.start_time for e in existing
        )
        if overlaps:
            continue

        review_entry = ScheduleEntry(
            user_id=user_id,
            topic_id=topic.id,
            subject_name=subject_name,
            topic_name=topic.name,
            scheduled_date=proposed_day,
            start_time=proposed_start,
            end_time=proposed_end,
            duration_mins=duration_mins,
            priority_score=1.2,
            is_revision=1,
            completed=0,
        )
        db.add(review_entry)
        return True

    return False


async def create_quiz(db: AsyncSession, req: QuizGenerateRequest) -> Quiz:
    """Generate a quiz for a given topic and difficulty."""
    topic = await db.get(Topic, req.topic_id)
    if not topic:
        raise ValueError("Topic not found for quiz generation")
    topic_name = topic.name
    subject = await db.get(Subject, topic.subject_id) if topic else None
    subject_name = subject.name if subject else None
    llm_subject_name = subject_name if req.use_subject_context else None
    related_topics: list[str] = []
    if topic:
        related_q = (
            select(Topic.name)
            .where(Topic.subject_id == topic.subject_id, Topic.id != topic.id)
            .limit(10)
        )
        related_result = await db.execute(related_q)
        related_topics = [name for name in related_result.scalars().all() if name]

    # Avoid reusing recent questions for this user/topic
    existing_q = (
        select(QuizQuestion.question_text)
        .join(Quiz, QuizQuestion.quiz_id == Quiz.id)
        .where(Quiz.user_id == req.user_id, Quiz.topic_id == req.topic_id)
    )
    existing_result = await db.execute(existing_q)
    used_questions = {_normalize_question_text(q) for q in existing_result.scalars().all() if q}

    # Groq-only generation: do not inject static question sets.
    attempts = 0
    questions_data: list[dict] = []
    last_error: str | None = None
    while len(questions_data) < req.num_questions and attempts < 2:
        remaining = req.num_questions - len(questions_data)
        requested = max(remaining + 2, remaining)
        try:
            batch = await asyncio.wait_for(
                _generate_questions_with_llm(
                    topic_name,
                    req.difficulty,
                    requested,
                    related_topics,
                    used_questions,
                    llm_subject_name,
                ),
                timeout=_QUIZ_BATCH_TIMEOUT_SECS,
            )
        except Exception as exc:
            last_error = str(exc)
            logger.exception(
                "LLM quiz generation failed for topic=%s difficulty=%s",
                topic_name,
                req.difficulty,
            )
            break

        questions_data.extend(batch)
        questions_data = _filter_and_backfill_questions(
            topic_name=topic_name,
            difficulty=req.difficulty,
            count=req.num_questions,
            related_topics=related_topics,
            used_questions=used_questions,
            questions=questions_data,
            allow_rule_backfill=False,
            subject_name=llm_subject_name,
        )
        used_questions = {_normalize_question_text(q["question_text"]) for q in questions_data}
        attempts += 1

    if len(questions_data) < req.num_questions and len(questions_data) < 3:
        detail = f"Groq could not prepare enough valid quiz questions for '{topic_name}' ({len(questions_data)}/{req.num_questions})"
        if last_error:
            detail = f"{detail}. Last error: {last_error}"
        raise RuntimeError(detail)

    # Persist only after Groq generation succeeds so SQLite is not held open
    # across long network calls.
    actual_question_count = len(questions_data)
    if actual_question_count < req.num_questions:
        logger.warning(
            "Proceeding with partial Groq quiz for topic=%s difficulty=%s count=%d requested=%d",
            topic_name,
            req.difficulty,
            actual_question_count,
            req.num_questions,
        )

    quiz = Quiz(
        user_id=req.user_id,
        topic_id=req.topic_id,
        difficulty=req.difficulty,
        total_questions=actual_question_count,
        included_in_progress=True,
    )
    db.add(quiz)
    await db.flush()  # get quiz.id

    for qd in questions_data:
        q = QuizQuestion(quiz_id=quiz.id, **qd)
        db.add(q)

    await db.flush()

    # Reload with questions
    result = await db.execute(
        select(Quiz).where(Quiz.id == quiz.id).options(selectinload(Quiz.questions))
    )
    return result.scalar_one()


async def evaluate_quiz(db: AsyncSession, req: QuizSubmitRequest) -> QuizResult:
    """Score submitted quiz answers and persist attempts."""
    quiz = await db.get(Quiz, req.quiz_id)
    if quiz is None:
        raise ValueError("Quiz not found")

    # Load questions
    q = select(QuizQuestion).where(QuizQuestion.quiz_id == quiz.id)
    result = await db.execute(q)
    questions = {qq.id: qq for qq in result.scalars().all()}

    correct = 0
    details: list[dict] = []

    for ans in req.answers:
        qq = questions.get(ans.question_id)
        if qq is None:
            continue
        is_correct = ans.answer.upper() == qq.correct_answer.upper()
        if is_correct:
            correct += 1

        attempt = QuizAttempt(
            quiz_id=quiz.id,
            question_id=ans.question_id,
            user_answer=ans.answer.upper(),
            is_correct=1 if is_correct else 0,
        )
        db.add(attempt)

        details.append({
            "question_id": ans.question_id,
            "your_answer": ans.answer,
            "correct_answer": qq.correct_answer,
            "is_correct": is_correct,
            "explanation": qq.explanation,
        })

    total = quiz.total_questions
    score_pct = (correct / total * 100) if total else 0.0
    quiz.score = score_pct
    difficulty = (quiz.difficulty or "medium").lower()
    threshold = PASS_THRESHOLDS.get(difficulty, PASS_THRESHOLDS["medium"])
    passed = score_pct >= threshold
    include_in_progress = req.include_in_progress
    quiz.included_in_progress = include_in_progress
    if include_in_progress:
        await _record_quiz_performance(db, req.user_id, difficulty, score_pct)
    next_quiz: Quiz | None = None
    quiz_time_spent_mins = max(total * 3, 10) if total else 0

    topic = await db.get(Topic, quiz.topic_id)
    review_created = False
    recommendation = (
        "Good work. Continue with the next topic."
        if include_in_progress
        else "Quiz score saved without changing study progress for this topic."
    )

    if topic and include_in_progress:
        topic.last_reviewed = datetime.now(timezone.utc)
        topic.time_spent_mins += quiz_time_spent_mins

    if include_in_progress and not passed:
        recommendation = (
            "Score below target. Read this topic again, then retake a quiz."
        )
        review_created = await _create_review_session(
            db, user_id=req.user_id, topic=topic
        )
        if topic:
            topic.completed = 0
            topic.completion_pct = max(topic.completion_pct - 10.0, 0.0)
    elif include_in_progress and topic and topic.completion_pct < 100:
        topic.completion_pct = min(topic.completion_pct + 5.0, 100.0)
        if topic.completion_pct >= 100:
            topic.completed = 1

    if include_in_progress and passed:
        next_difficulty = {
            "easy": "medium",
            "medium": "hard",
        }.get(difficulty)
        if next_difficulty:
            try:
                next_quiz = await asyncio.wait_for(
                    create_quiz(
                        db,
                        QuizGenerateRequest(
                            user_id=req.user_id,
                            topic_id=quiz.topic_id,
                            difficulty=next_difficulty,
                            num_questions=quiz.total_questions,
                        ),
                    ),
                    timeout=12.0,
                )
                recommendation = (
                    f"Good work. Your {next_difficulty} level quiz is ready."
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Automatic next-level quiz generation timed out for quiz %s",
                    quiz.id,
                )
                recommendation = (
                    "Good work. Score saved successfully. Generate the next level quiz manually."
                )
            except Exception:
                logger.exception(
                    "Automatic next-level quiz generation failed for quiz %s",
                    quiz.id,
                )
                recommendation = (
                    "Good work. Score saved successfully. Generate the next level quiz manually."
                )

    if include_in_progress and (not passed or score_pct < 60):
        try:
            reflection = await AdaptiveAgent(db, req.user_id).run()
            if reflection.schedule_entries_created > 0:
                recommendation = (
                    f"{recommendation} Your upcoming schedule was rescheduled to reinforce weaker areas."
                )
        except Exception:
            logger.exception(
                "Adaptive rescheduling failed after quiz evaluation for quiz %s",
                quiz.id,
            )

    if include_in_progress:
        progress = ProgressRecord(
            user_id=req.user_id,
            topic_id=quiz.topic_id,
            quiz_score=score_pct,
            time_spent_mins=quiz_time_spent_mins,
            completion_pct=topic.completion_pct if topic else 0,
            notes=f"Quiz {difficulty} score: {round(score_pct, 1)}%",
        )
        db.add(progress)

    await db.flush()

    return QuizResult(
        quiz_id=quiz.id,
        total_questions=total,
        correct_count=correct,
        score_pct=round(score_pct, 1),
        passed=passed,
        pass_threshold=threshold,
        recommendation=recommendation,
        review_session_created=review_created,
        next_quiz=next_quiz,
        details=details,
    )


async def _record_quiz_performance(
    db: AsyncSession, user_id: str, difficulty: str, score_pct: float
) -> None:
    stmt = (
        select(QuizPerformance)
        .where(
            QuizPerformance.user_id == user_id,
            QuizPerformance.difficulty == difficulty,
        )
        .with_for_update()
    )
    with db.no_autoflush:
        result = await db.execute(stmt)
    perf = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if perf:
        prev_attempts = perf.attempts or 0
        perf.attempts = prev_attempts + 1
        perf.last_score = score_pct
        perf.best_score = max(perf.best_score or 0.0, score_pct)
        prev_avg = perf.average_score or 0.0
        perf.average_score = (
            (prev_avg * prev_attempts + score_pct) / perf.attempts
            if perf.attempts
            else score_pct
        )
        perf.last_attempted = now
    else:
        perf = QuizPerformance(
            user_id=user_id,
            difficulty=difficulty,
            attempts=1,
            best_score=score_pct,
            average_score=score_pct,
            last_score=score_pct,
            last_attempted=now,
        )
        db.add(perf)
