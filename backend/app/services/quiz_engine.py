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
from backend.app.schemas.quiz import (
    QuizGenerateRequest,
    QuizSubmitRequest,
    QuizResult,
)


_UNIT_PREFIX_RE = re.compile(r"^\s*(Unit\s+[IVXLC\d]+)\s*:\s*(.+)$", re.IGNORECASE)
_PART_SPLIT_RE = re.compile(r"\s*(?:,|;|\||/|&|\band\b)\s*", re.IGNORECASE)
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

# Keep outbound LLM calls fast so the UI never times out
_LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=8.0, pool=None)


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
    if subject_name:
        subject_key = _normalize_question_text(subject_name)
        if subject_key and len(subject_key) >= 4 and subject_key in normalized_q:
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
    banned_fragments = [
        "which unit",
        "unit i",
        "unit ii",
        "unit iii",
        "unit iv",
        "which chapter",
        "in the syllabus",
        "mapped to which",
        "what is this topic called",
        "choose the topic name",
        "learning outcome",
        "best learning outcome",
        "key concept check",
        "before an exam",
        "revision steps",
        "mapped to your syllabus",
        "only headings",
        "guess answers",
    ]
    return any(fragment in normalized for fragment in banned_fragments)


def _has_bad_option(*options: str) -> bool:
    """Filter out generic or meta options that make quizzes feel canned."""
    banned_fragments = [
        "unrelated",
        "random memorization",
        "guess answers",
        "only headings",
        "mapped to",
        "unit ",
        "syllabus",
        "before an exam",
        "best learning outcome",
    ]
    for opt in options:
        normalized = _normalize_question_text(opt)
        if any(fragment in normalized for fragment in banned_fragments):
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
    """Utility for synchronous rule-based question generation."""
    return _generate_contextual_rule_based_questions(
        topic_name, difficulty, count, []
    )


def _generate_contextual_rule_based_questions(
    topic_name: str,
    difficulty: str,
    count: int,
    related_topics: list[str],
    used_questions: set[str] | None = None,
) -> list[dict]:
    """Generate topic-grounded MCQs without an LLM."""
    body, unit_label = _strip_unit_prefix(topic_name)
    topic_parts = _split_topic_parts(topic_name)
    related_clean = [_strip_unit_prefix(t)[0] for t in related_topics if t]
    # Keep only related topics that actually share wording with the target topic
    related_clean = _filter_related_by_overlap(topic_parts or [body], related_clean)
    related_parts: list[str] = []
    for related in related_clean:
        related_parts.extend(_split_topic_parts(related))

    if not topic_parts:
        topic_parts = [body or topic_name]

    generators: list[dict[str, Any]] = []

    core = topic_parts[0]
    generators.append(
        {
            "stem": f"In the context of '{body}', which concept is explicitly part of this topic?",
            "correct": core,
            "distractors": (related_parts + related_clean)[:3] or _fallback_distractors(core),
            "explanation": f"'{core}' sits inside the scope of '{body}'.",
        }
    )

    # Difficulty-tuned stems added to avoid repetition across levels and better align rigor
    difficulty_profiles = {
        "easy": [
            {
                "stem": f"What is the primary definition associated with '{core}'?",
                "correct": f"The standard meaning of {core}",
                "distractors": (related_clean[:3] or []) + _fallback_distractors(core),
                "explanation": "Easy level checks core definition recall.",
            },
            {
                "stem": f"Select the most basic example of '{core}'.",
                "correct": f"A textbook example illustrating {core}",
                "distractors": related_clean[:4] + _fallback_distractors(core),
                "explanation": "Examples anchor basic understanding.",
            },
        ],
        "medium": [
            {
                "stem": f"How would you apply '{core}' to solve a standard problem?",
                "correct": f"Use {core} directly following its usual steps",
            "distractors": (related_clean[:3] or []) + _fallback_distractors(core),
            "explanation": "Medium level checks application of known steps.",
        },
        {
            "stem": f"Which mistake is common when using '{core}'?",
            "correct": f"Forgetting a key condition when applying {core}",
                "distractors": (related_clean[:3] or []) + _fallback_distractors(core),
                "explanation": "Targets misconception detection.",
            },
        ],
        "hard": [
            {
                "stem": f"When analyzing a complex case, how does '{core}' interact with related topics?",
                "correct": f"It must be combined with {related_clean[0] if related_clean else 'its prerequisites'} under given constraints",
                "distractors": (related_clean[:4] or []) + _fallback_distractors(core),
                "explanation": "Hard level checks integration and assumptions.",
            },
            {
                "stem": f"Given a failure scenario using '{core}', what is the best remediation?",
                "correct": f"Review the boundary conditions and adjust {core} accordingly",
                "distractors": (related_clean[:4] or []) + _fallback_distractors(core),
                "explanation": "Hard level focuses on troubleshooting and refinement.",
            },
        ],
    }

    if unit_label:
        unit_token = unit_label.split()[-1]
        generators.append(
            {
                "stem": f"When reviewing '{body}', which nearby topic strengthens understanding?",
                "correct": related_clean[0] if related_clean else body,
                "distractors": (related_clean[1:4] if len(related_clean) > 1 else []) + _fallback_distractors(core),
                "explanation": "Connect the topic to an adjacent concept instead of meta unit mapping.",
            }
        )

    focus_phrase = topic_parts[min(1, len(topic_parts) - 1)]
    mastery_verb = {
        "easy": "identify",
        "medium": "apply",
        "hard": "analyze",
    }.get(difficulty.lower(), "apply")
    generators.append(
        {
            "stem": f"Best learning outcome after studying '{body}' is to ______.",
            "correct": f"{mastery_verb} {focus_phrase} correctly in real problems",
            "distractors": _fallback_distractors(core),
            "explanation": f"Mastery means you can {mastery_verb} '{focus_phrase}', not just recall headings.",
        }
    )

    if related_clean:
        related_pick = related_clean[0]
        generators.append(
            {
                "stem": f"During revision of '{body}', which related topic should you connect for better retention?",
                "correct": related_pick,
                "distractors": (topic_parts if topic_parts else []) + _fallback_distractors(core),
                "explanation": f"Linking to '{related_pick}' reinforces conceptual context.",
            }
        )

    generators.append(
        {
            "stem": f"What is the most effective way to revise '{body}' before an exam?",
            "correct": f"Break it into key parts ({', '.join(topic_parts[:2])}) and do active recall",
            "distractors": [
                "Skim once and avoid self-testing",
                "Only read solved answers from other subjects",
                "Skip it because it feels familiar",
            ],
            "explanation": "Chunking plus self-testing yields higher retention.",
        }
    )

    # Broader stem pool to reduce fallback/fill usage when many prior questions exist
    extra_generators = [
        {
            "stem": f"Which option directly belongs under '{body}'?",
            "correct": core,
            "distractors": related_clean[:2] + _fallback_distractors(core),
            "explanation": f"'{core}' is central to '{body}'.",
        },
        {
            "stem": f"What is the first step when approaching a practice problem on '{body}'?",
            "correct": f"Identify the relevant concept: {core}",
            "distractors": [
                "Guess without reading the question",
                "Start with an unrelated topic",
                "Skip to the solution key immediately",
            ],
            "explanation": "Locating the core concept is the right starting point.",
        },
        {
            "stem": f"A common pitfall when revising '{body}' is:",
            "correct": "Memorizing steps without understanding conditions",
            "distractors": [
                "Checking worked examples",
                "Practicing spaced repetition",
                "Reviewing past mistakes",
            ],
            "explanation": "Shallow memorization leads to errors under variation.",
        },
        {
            "stem": f"To boost retention for '{body}', you should:",
            "correct": f"Connect it with {related_clean[0] if related_clean else 'its prerequisites'} and quiz yourself",
            "distractors": [
                "Avoid all practice questions",
                "Rely only on highlights",
                "Study unrelated chapters first",
            ],
            "explanation": "Linking related ideas plus self-testing improves recall.",
        },
        {
            "stem": f"In assessments, '{body}' is most likely evaluated by asking you to:",
            "correct": f"Apply {core} to a short scenario",
            "distractors": [
                "List unrelated trivia",
                "Describe an unrelated field",
                "Ignore given constraints",
            ],
            "explanation": "Assessments test applied understanding, not trivia.",
        },
    ]
    generators.extend(extra_generators)

    # Difficulty-flavored scenario/item
    generators.append(
        {
            "stem": f"You need to apply '{body}' in a real case study. What should you focus on first?",
            "correct": core,
            "distractors": related_clean[:2] + _fallback_distractors(core),
            "explanation": f"Start with the core element '{core}' to structure the solution.",
        }
    )

    # Append difficulty-specific generators so they rotate into the selection loop
    generators.extend(difficulty_profiles.get(difficulty.lower(), []))

    questions: list[dict[str, Any]] = []
    used_norm = set(used_questions or set())
    seen_stems: set[str] = set()
    idx = 0
    attempts = 0
    max_attempts = max(count * 6, 15)

    while len(questions) < count and attempts < max_attempts:
        attempts += 1
        template = generators[idx % len(generators)]
        idx += 1
        stem = template["stem"]
        norm = _normalize_question_text(stem)
        if norm in used_norm:
            continue
        used_norm.add(norm)
        questions.append(
            _topic_grounded_mcq(
                stem=stem,
                correct=template["correct"],
                distractors=template["distractors"],
                explanation=template["explanation"],
                order_index=len(questions),
            )
        )

    # Guaranteed fill: rotate through varied stems instead of numbered "scenario" repeats.
    fallback_templates = [
        f"What is the key idea behind '{core}'?",
        f"Which statement is true about '{core}' in practice?",
        f"Which example best illustrates correct use of '{core}'?",
        f"What is the first thing to check before applying '{core}'?",
        f"What common mistake should you avoid when using '{core}'?",
        f"Which option directly relates to '{core}'?",
        f"Which prerequisite best supports learning '{core}'?",
        f"Where would '{core}' be applied in a real system?",
    ]
    fb_idx = 0
    while len(questions) < count:
        stem = fallback_templates[fb_idx % len(fallback_templates)]
        fb_idx += 1
        questions.append(
            _topic_grounded_mcq(
                stem=stem,
                correct=core,
                distractors=_fallback_distractors(core),
                explanation="Fallback variant to complete the quiz without duplicating stems.",
                order_index=len(questions),
            )
        )

    return questions


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
    """Remove low-quality/off-topic/duplicate questions then backfill with rule-based ones to reach count."""
    filtered: list[dict] = []
    used = used_questions if used_questions is not None else set()
    seen: set[str] = set()

    for q in questions:
        qtext = q.get("question_text", "")
        if _is_low_quality_question(qtext):
            continue
        if not _is_question_on_topic(qtext, topic_name, subject_name):
            continue
        key = _normalize_question_text(qtext)
        if key in seen or key in used:
            continue
        seen.add(key)
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
            seen_keys.add(key)
            used.add(key)
            ans = str(item.get("correct_answer", "A")).upper()
            if ans not in {"A", "B", "C", "D"}:
                ans = "A"
            cleaned.append(
                {
                    "question_text": qtext,
                    "option_a": str(item.get("option_a", "")),
                    "option_b": str(item.get("option_b", "")),
                    "option_c": str(item.get("option_c", "")),
                    "option_d": str(item.get("option_d", "")),
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
        if allow_rule_backfill and len(filtered) < count:
            backfill = _generate_contextual_rule_based_questions(
                topic_name, difficulty, count - len(filtered), related_topics, used
            )
            for item in backfill:
                item["order_index"] = len(filtered)
                filtered.append(item)
                if len(filtered) >= count:
                    break

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
    provider = (settings.ai_provider or "rule_based").lower()
    require_groq = getattr(settings, "require_groq", False)

    if provider == "rule_based":
        # Prefer LLM if keys are available; otherwise, fail fast instead of returning static templates.
        if settings.openai_api_key:
            provider = "openai"
        elif settings.groq_api_key:
            provider = "groq"
        else:
            raise RuntimeError("AI provider not configured; set AI_PROVIDER=openai or groq and provide an API key")

    if provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError("Groq API key is missing")
        try:
            return await _generate_questions_with_groq(
                topic_name, difficulty, count, related_topics, used_questions, subject_name
            )
        except Exception:
            if require_groq:
                # Surface the error so the caller shows failure instead of silent fallback
                raise
            logger.warning(
                "Groq generation failed; falling back to rule-based because require_groq=False"
            )
            return _generate_contextual_rule_based_questions(
                topic_name, difficulty, count, related_topics, used_questions
            )

    if provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OpenAI API key is missing")
        return await _generate_questions_with_openai(
            topic_name, difficulty, count, related_topics, used_questions, subject_name
        )

    if require_groq:
        raise RuntimeError("require_groq=True but AI_PROVIDER is not 'groq'")

    # No valid provider after checks
    raise RuntimeError("AI provider not configured; set AI_PROVIDER=openai or groq and provide an API key")


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
    except Exception:
        return _generate_contextual_rule_based_questions(
            topic_name, difficulty, count, related_topics
        )

    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return _generate_contextual_rule_based_questions(
            topic_name, difficulty, count, related_topics
        )

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return _generate_contextual_rule_based_questions(
            topic_name, difficulty, count, related_topics
        )

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

    if len(formatted) < count:
        fallback = _generate_contextual_rule_based_questions(
            topic_name, difficulty, count - len(formatted), related_topics, used
        )
        for item in fallback:
            item["order_index"] = len(formatted)
            formatted.append(item)

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
        "Return JSON array where each item has: question_text, option_a, option_b, option_c, option_d, "
        "correct_answer (A/B/C/D), explanation."
    )

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
            for attempt in range(3):
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.groq_model,
                        "temperature": 0.4,
                        "max_tokens": 800,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                # Handle rate limits with short backoff
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", "2"))
                    wait = max(retry_after, 2 * (attempt + 1))
                    logger.warning(
                        "Groq rate limit (attempt %d), retrying in %.1fs: %s",
                        attempt + 1,
                        wait,
                        resp.text,
                    )
                    if attempt == 2:
                        break
                    await asyncio.sleep(wait)
                    continue
                # Retry transient 5xx once
                if resp.status_code >= 500 and attempt < 2:
                    logger.warning(
                        "Groq transient error %s, retrying: %s", resp.status_code, resp.text
                    )
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break

            if resp is None:
                raise RuntimeError("Groq did not return a response")

            if resp.status_code == 429:
                if getattr(settings, "require_groq", False):
                    resp.raise_for_status()
                else:
                    raise RuntimeError("Groq rate limited")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("choices"):
                logger.error("Groq response missing choices: %s", data)
                raise ValueError("Groq response missing choices array")
            content = data["choices"][0]["message"]["content"]
            logger.info("Groq response received length=%d", len(content or ""))
    except Exception as exc:
        body = None
        try:
            body = resp.text  # type: ignore[name-defined]
        except Exception:
            pass
        logger.exception("Groq quiz generation failed. Response body: %s", body)
        raise RuntimeError("Groq quiz generation failed") from exc

    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        logger.warning("Groq content missing JSON array")
        raise ValueError("Groq response missing JSON array of questions")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("Groq JSON parse failed")
        raise ValueError("Groq response JSON parse failed")

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

    if len(formatted) < count:
        # Prefer salvaging more Groq items (even if they failed earlier filters) before rule-based fill
        for item in parsed:
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

        if len(formatted) < count and not getattr(settings, "require_groq", False):
            fallback = _generate_contextual_rule_based_questions(
                topic_name, difficulty, count - len(formatted), related_topics, used
            )
            for item in fallback:
                item["order_index"] = len(formatted)
                formatted.append(item)

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

    quiz = Quiz(
        user_id=req.user_id,
        topic_id=req.topic_id,
        difficulty=req.difficulty,
        total_questions=req.num_questions,
    )
    db.add(quiz)
    await db.flush()  # get quiz.id

    # Prefer LLM generation, but fall back to rule-based generation so quiz entry points
    # from schedule/subjects remain reliable even when the provider is unavailable.
    attempts = 0
    questions_data: list[dict] = []
    llm_failed = False
    while len(questions_data) < req.num_questions and attempts < 3:
        remaining = req.num_questions - len(questions_data)
        requested = max(remaining + 1, remaining)
        try:
            batch = await _generate_questions_with_llm(
                topic_name,
                req.difficulty,
                requested,
                related_topics,
                used_questions,
                subject_name,
            )
        except Exception:
            llm_failed = True
            logger.exception(
                "LLM quiz generation failed for topic=%s difficulty=%s; will use fallback if needed",
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
            subject_name=subject_name,
        )
        used_questions = {_normalize_question_text(q["question_text"]) for q in questions_data}
        attempts += 1

    if len(questions_data) < req.num_questions:
        logger.warning(
            "Quiz fallback activated for topic=%s difficulty=%s generated=%d requested=%d llm_failed=%s",
            topic_name,
            req.difficulty,
            len(questions_data),
            req.num_questions,
            llm_failed,
        )
        questions_data = _filter_and_backfill_questions(
            topic_name=topic_name,
            difficulty=req.difficulty,
            count=req.num_questions,
            related_topics=related_topics,
            used_questions=used_questions,
            questions=questions_data,
            allow_rule_backfill=True,
            subject_name=subject_name,
        )

    if len(questions_data) < req.num_questions:
        raise RuntimeError(
            f"Could not generate enough quiz questions ({len(questions_data)}/{req.num_questions})"
        )

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
    await _record_quiz_performance(db, req.user_id, difficulty, score_pct)
    next_quiz: Quiz | None = None

    topic = await db.get(Topic, quiz.topic_id)
    review_created = False
    recommendation = "Good work. Continue with the next topic."

    # Also record in progress
    progress = ProgressRecord(
        user_id=req.user_id,
        topic_id=quiz.topic_id,
        quiz_score=score_pct,
        time_spent_mins=0,
        completion_pct=topic.completion_pct if topic else 0,
        notes=f"Quiz {difficulty} score: {round(score_pct, 1)}%",
    )
    db.add(progress)

    if topic:
        topic.last_reviewed = datetime.now(timezone.utc)

    if not passed:
        recommendation = (
            "Score below target. Read this topic again, then retake a quiz."
        )
        review_created = await _create_review_session(
            db, user_id=req.user_id, topic=topic
        )
        if topic:
            topic.completed = 0
            topic.completion_pct = max(topic.completion_pct - 10.0, 0.0)
    elif topic and topic.completion_pct < 100:
        topic.completion_pct = min(topic.completion_pct + 5.0, 100.0)
        if topic.completion_pct >= 100:
            topic.completed = 1

    if passed:
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

