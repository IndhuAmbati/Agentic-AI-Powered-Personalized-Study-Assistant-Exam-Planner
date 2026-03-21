"""Educational chatbot API."""

from __future__ import annotations

from collections import defaultdict
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.models.chat import ChatMessage as ChatMessageModel
from backend.app.models.schedule import ScheduleEntry
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.schemas.chat import ChatAskRequest, ChatAskResponse, ChatHistoryItem, ChatMessage

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _looks_truncated_answer(text: str) -> bool:
    if not text:
        return False
    stripped = text.rstrip()
    if len(stripped) < 80:
        return False
    if stripped.endswith((":", "-", "*", "(", "/", "\\")):
        return True
    if re.search(r"[A-Za-z0-9][A-Za-z]{0,4}$", stripped) and not stripped.endswith((".", "!", "?", '"', "'")):
        return True
   


def _merge_continuation(base: str, extra: str) -> str:
    if not base:
        return extra
    if not extra:
        return base
    merged = extra.lstrip()
    max_overlap = min(len(base), len(merged), 240)
    for size in range(max_overlap, 20, -1):
        if base[-size:] == merged[:size]:
            merged = merged[size:]
            break
    return f"{base.rstrip()}\n{merged.lstrip()}".strip()


def _extract_multi_topics(message: str) -> list[str]:
    query = (message or "").strip().lower()
    cleaned = query.replace("\n", " ")
    cleaned = re.sub(r"\b(explain|xplain|what is|what are|define|means)\b", " ", cleaned)
    cleaned = re.sub(r"\balgorithms?\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if not cleaned:
        return []
    parts = re.split(r",|\band\b|/|\|", cleaned)
    topics: list[str] = []
    for part in parts:
        topic = part.strip(" ,-")
        if not topic:
            continue
        topic = re.sub(r"\bplease\b", " ", topic)
        topic = re.sub(r"\s+", " ", topic).strip()
        if topic == "a*":
            topic = "A*"
        elif "*" in topic:
            topic = topic.upper()
        if topic and topic not in topics:
            topics.append(topic)
    return topics[:5]


def _wants_detailed_answer(message: str) -> bool:
    query = f" {_normalize_educational_query(message)} "
    detail_terms = {
        "in detail",
        "detailed",
        "deep",
        "deeply",
        "elaborate",
        "full answer",
        "long answer",
        "explain deeply",
        "step by step",
    }
    return any(f" {term} " in query for term in detail_terms)


def _normalize_educational_query(text: str) -> str:
    normalized = f" {_normalize(text)} "
   
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"\s+", " ", normalized).strip()


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _normalize(text).split() if len(tok) >= 2}


def _subject_aliases(name: str) -> set[str]:
    normalized = _normalize(name)
    aliases = {normalized}
    code_match = re.match(r"^([a-z0-9 ]+?)\s+-\s+", normalized)
    if code_match:
        aliases.add(code_match.group(1).strip())
        aliases.add(normalized.split("-", 1)[1].strip())
    return {alias for alias in aliases if alias}


def _format_schedule_line(entry: ScheduleEntry) -> str:
    return (
        f"{entry.scheduled_date} {str(entry.start_time)[:5]}-{str(entry.end_time)[:5]}: "
        f"{entry.subject_name} - {entry.topic_name}"
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_educational_query(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {_normalize_educational_query(text)} "


def _question_intent(message: str) -> str:
    query = _normalize_educational_query(message)
    if not query:
        return "other"
    if any(token in f" {query} " for token in [" code ", " generate ", " write ", " implement "]):
        return "code"
    if "difference between" in query or "compare" in query:
        return "compare"
    if "types of" in query:
        return "types"
    if "how do i prepare" in query or "how should i prepare" in query or "exam in one week" in query:
        return "exam_plan"
    if query.startswith("how ") or " how " in f" {query} ":
        return "how"
    if (
        query.startswith("what is")
        or query.startswith("define")
        or query.startswith("explain")
        or query.startswith("xplain")
        or " explain " in f" {query} "
        or " xplain " in f" {query} "
    ):
        return "explain"
    return "other"
}


def _extract_focus_phrase(message: str) -> str:
    query = _normalize_educational_query(message)
    prefixes = [
        "what is ",
        "what are ",
        "define ",
        "explain ",
        "types of ",
        "difference between ",
        "compare ",
        "how does ",
        "how do ",
        "how to ",
    ]
    focus = query
    for prefix in prefixes:
        if focus.startswith(prefix):
            focus = focus[len(prefix):]
            break
    focus = re.sub(r"\bplease\b", " ", focus)
    focus = re.sub(r"\s+", " ", focus).strip(" ?.-")
    return focus or query

def _format_focus_title(focus: str) -> str:
    if not focus:
        return "This topic"
    words = [word.upper() if len(word) <= 4 else word.capitalize() for word in focus.split()]
    return " ".join(words)


def _build_dynamic_educational_answer(
    message: str,
    subjects: list[Subject],
) -> tuple[str, list[str], list[str]] | None:
    intent = _question_intent(message)
    if intent not in {"explain", "types", "compare", "how", "exam_plan", "code"}:
        return None

    focus = _extract_focus_phrase(message)
    query = _normalize_educational_query(message)
    domain_key = _infer_domain_key(message, subjects)
    frame = DOMAIN_FRAMES.get(domain_key, DOMAIN_FRAMES["general"])
    label = str(frame["label"])
    core = list(frame["core"])
    applications = list(frame["applications"])
    study_tip = str(frame["study_tip"])
    suggestions = list(frame["suggestions"])
    focus_title = _format_focus_title(focus)

def _enhance_fallback_answer(
    message: str,
    answer: str,
    sources: list[str],
    suggestions: list[str],
) -> tuple[str, list[str], list[str]]:
    return answer, sources, suggestions

def _is_study_data_query(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False
    study_data_terms = {
        "my subject",
        "my subjects",
        "list my subjects",
        "show my subjects",
        "show topics",
        "list topics",
        "my topics",
        "my schedule",
        "my timetable",
        "today",
        "next session",
        "next sessions",
        "what should i study next",
        "revise this week",
        "my unit",
    }
    return any(_contains_phrase(query, term) for term in study_data_terms)


def _llm_unavailable_answer() -> tuple[str, list[str], list[str]]:
    return (
        "Educational AI is not available right now. Configure a working Groq API key in your `.env` file to get full GPT-style educational answers.",
        ["llm_required"],
        ["List my subjects", "Show my schedule", "What should I study next?"],
    )


def _llm_temporarily_unavailable_answer() -> tuple[str, list[str], list[str]]:
    return (
        "Groq is temporarily unavailable right now, so I could not generate an LLM answer for this educational question. Please try again in a moment.",
        ["groq_unavailable"],
        ["Retry this question", "Show my schedule", "List my subjects"],
    )




def _is_non_educational_query(message: str) -> bool:
    query = _normalize_educational_query(message)
    if not query:
        return False

    non_educational_terms = {
        "trip",
        "trips",
        "travel",
        "tour",
        "tourism",
        "vacation",
        "holiday",
        "hotel",
        "flight",
        "restaurant",
        "food",
        "cooking",
        "recipe",
        "recipes",
        "movie",
        "song",
        "lyrics",
        "celebrity",
        "actor",
        "actress",
        "cricket score",
        "match score",
        "world cup",
        "t20 world cup",
        "who won",
        "ipl",
        "football match",
        "weather",
        "bitcoin price",
        "stock price",
        "shopping",
        "shopping list",
        "buy",
        "amazon",
        "flipkart",
        "motivation",
    }
    return any(_contains_phrase(query, term) for term in non_educational_terms)


def _is_compliment(message: str) -> bool:
    """Detect short appreciative/compliment messages."""
    query = f" {_normalize_educational_query(message)} "
    compliment_terms = [
        "good bot",
        "great",
        "awesome",
        "nice",
        "well done",
        "thanks",
        "thank you",
        "appreciate",
        "helpful",
        "so good",
        "good job",
        "cool",
    ]
    return any(term in query for term in compliment_terms)


def _non_educational_redirect() -> tuple[str, list[str], list[str]]:
    return (
        "I am restricted to educational help only. Ask about concepts, formulas, programming, exam preparation, study methods, or your syllabus and timetable.",
        ["education_only"],
        ["Explain graphs", "What is data mining?", "How do I revise Unit 1?"],
    )


def _educational_clarification_redirect() -> tuple[str, list[str], list[str]]:
    return (
        "Your question looks educational, but it is too broad or ambiguous. Ask with a little more context, such as the subject or exact concept you want.",
        ["education_clarification"],
        ["Explain graphs in data structures", "Explain trees in DSA", "Explain slope formula in maths"],
    )


def _general_educational_answer(message: str) -> tuple[str, list[str], list[str]] | None:
    query = _normalize_educational_query(message)
    expanded_query = f" {query} "
    expanded_query = expanded_query.replace(" da ", " data analytics ")
    if _is_compliment(message):
        return (
            "Thanks! Ask any educational question or tell me a subject/unit to focus on.",
            ["general_education"],
            ["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
        )
    if any(greeting in expanded_query for greeting in [" hi ", " hello ", " hey "]):
        return (
            "Ask any educational question, concept doubt, exam-prep question, or study-method question. "
            "I can explain topics across programming, CS, analytics, economics, math, and more.",
            ["general_education"],
            ["Explain machine learning types", "What is normalization in DBMS?", "How do I prepare for exams?"],
        )

    explain_like = any(
        phrase in query
        for phrase in [
            "explain",
            "what is",
            "what are",
            "types of",
            "define",
            "difference between",
            "compare",
            "how does",
            "how do",
            "why",
            "advantages",
            "disadvantages",
            "example",
            "examples",
        ]
    )

    dynamic_answer = _build_dynamic_educational_answer(message, [])
    if dynamic_answer is not None:
        return dynamic_answer

    if not explain_like and not _is_general_educational_query(message):
        return None

  
    general_answer = _general_educational_answer(message)
    if general_answer is not None:
        return general_answer

    if {"today", "now", "next"} & tokens or "study" in tokens or "schedule" in tokens or "timetable" in tokens:
        if not upcoming_entries:
            return (
                "No upcoming study sessions are scheduled right now.",
                ["schedule"],
                ["Generate a timetable", "List my subjects"],
            )
        next_items = upcoming_entries[:5]
        answer = "Your next study sessions are:\n" + "\n".join(
            f"- {_format_schedule_line(entry)}" for entry in next_items
        )
        return answer, ["schedule"], ["Show topics in a subject", "What should I revise this week?"]

    if "subject" in tokens and ("list" in tokens or "what" in tokens or "have" in tokens):
        if not subjects:
            return (
                "No subjects are saved yet.",
                ["subjects"],
                ["Import syllabus", "Add a subject"],
            )
        return (
            "Your subjects are:\n" + "\n".join(f"- {subject.name}" for subject in subjects),
            ["subjects"],
            [f"Show topics in {subjects[0].name}" if subjects else "Show my timetable"],
        )

    matched_subjects: list[Subject] = []
    for subject in subjects:
        aliases = _subject_aliases(subject.name)
        if any(alias in query for alias in aliases) or (_tokenize(subject.name) & tokens):
            matched_subjects.append(subject)

    if matched_subjects:
        subject = matched_subjects[0]
        ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
        if "topic" in tokens or "syllabus" in tokens or "unit" in tokens or "cover" in tokens:
            topic_lines = [f"- {topic.name}" for topic in ordered_topics[:25]]
            if len(ordered_topics) > 25:
                topic_lines.append(f"- ... and {len(ordered_topics) - 25} more topics")
            return (
                f"{subject.name} has {len(ordered_topics)} topics:\n" + "\n".join(topic_lines),
                [subject.name],
                [f"What should I study next in {subject.name}?", f"How do I revise {subject.name}?"],
            )

        completed = sum(1 for topic in ordered_topics if topic.completed)
        weak_topics = [topic.name for topic in ordered_topics if topic.completion_pct < 40][:5]
        answer_lines = [
            f"{subject.name} has {len(ordered_topics)} topics and {completed} completed topics.",
        ]
        if weak_topics:
            answer_lines.append("Focus next on: " + ", ".join(weak_topics))
        next_subject_sessions = [entry for entry in upcoming_entries if entry.subject_name == subject.name][:3]
        if next_subject_sessions:
            answer_lines.append("Upcoming sessions:")
            answer_lines.extend(f"- {_format_schedule_line(entry)}" for entry in next_subject_sessions)
        return (
            "\n".join(answer_lines),
            [subject.name, "schedule"],
            [f"Show topics in {subject.name}", f"Explain {subject.name} study plan"],
        )

    matched_topics: list[tuple[str, str]] = []
    for subject in subjects:
        for topic in subject.topics:
            topic_tokens = _tokenize(topic.name)
            if len(topic_tokens & tokens) >= 2 or _normalize(topic.name) in query:
                matched_topics.append((subject.name, topic.name))

    if matched_topics:
        grouped: dict[str, list[str]] = defaultdict(list)
        for subject_name, topic_name in matched_topics[:10]:
            grouped[subject_name].append(topic_name)
        lines = ["I found these matching topics:"]
        for subject_name, topic_names in grouped.items():
            lines.append(f"- {subject_name}: {', '.join(topic_names)}")
        return "\n".join(lines), ["topics"], ["Show my next sessions", "List my subjects"]

    educational_guidance = {
        "revision": "For revision, use active recall, short unit-wise notes, and quiz practice after each unit.",
        "quiz": "For quizzes, first review the unit summary, then solve 5-10 questions and check explanations.",
        "exam": "For exam prep, prioritize high-weight units, weak topics, and one revision cycle before the exam.",
        "study": "Use 45-60 minute focused sessions, then a short break. End each session with 3 key takeaways.",
        "process": "Explain the topic in your own words, write key points, solve one example, then test yourself without notes.",
        "algorithm": "For algorithm questions, describe the idea first, then steps, time complexity, space complexity, and one example.",
        "python": "For Python learning, start with syntax, functions, lists/dicts, file handling, and small practice problems.",
        "database": "For database topics, focus on ER modeling, normalization, SQL queries, joins, indexing, and transactions.",
    }
    for key, value in educational_guidance.items():
        if key in tokens:
            return value, ["guidance"], ["What should I study next?", "Show weak topics in a subject"]

    return (
        "I can answer educational questions in general, and I can also use your saved subjects, topics, units, "
        "and timetable when relevant. Ask a concept question, exam-prep question, or study-planning question.",
        [],
        [
            "Explain normalization in DBMS",
            "How do I prepare for an exam in one week?",
            "List my subjects",
        ],
    )


# Runtime fallback: prefer real educational answers even when the LLM is unavailable.
def _general_educational_answer(
    message: str,
    subjects: list[Subject] | None = None,
) -> tuple[str, list[str], list[str]] | None:
    query = _normalize_educational_query(message)
    expanded_query = f" {query} "
    subjects = subjects or []

    if _is_compliment(message):
        return (
            "Thanks! Ask any educational question or tell me a subject or unit to focus on.",
            ["general_education"],
            ["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
        )

    if any(greeting in expanded_query for greeting in [" hi ", " hello ", " hey "]):
        return (
            "Ask any educational question, concept doubt, exam-prep question, or study-method question. "
            "I can explain topics across programming, computer science, analytics, economics, maths, and more.",
            ["general_education"],
            ["Explain machine learning types", "What is normalization in DBMS?", "How do I prepare for exams?"],
        )

    if _needs_educational_clarification(message):
        return _educational_clarification_redirect()

    dynamic_answer = _build_dynamic_educational_answer(message, subjects)
    if dynamic_answer is not None:
        return dynamic_answer



def _rule_based_answer(
    message: str,
    subjects: list[Subject],
    upcoming_entries: list[ScheduleEntry],
) -> tuple[str, list[str], list[str]]:
    query = _normalize(message)
    tokens = _tokenize(message)

    if not query:
        return (
            "Ask about your saved subjects, topics, timetable, or revision status.",
            ["study_data"],
            ["List my subjects", "Show my schedule", "What should I study next?"],
        )

    if _is_non_educational_query(message):
        return _non_educational_redirect()

    general_answer = _general_educational_answer(message, subjects)
    if general_answer is not None:
        return general_answer

    if {"today", "now", "next"} & tokens or "schedule" in tokens or "timetable" in tokens:
        if not upcoming_entries:
            return (
                "No upcoming study sessions are scheduled right now.",
                ["schedule"],
                ["List my subjects", "Show topics in a subject"],
            )
        next_items = upcoming_entries[:5]
        answer = "Your next study sessions are:\n" + "\n".join(
            f"- {_format_schedule_line(entry)}" for entry in next_items
        )
        return answer, ["schedule"], ["List my subjects", "Show topics in a subject"]

    if "subject" in tokens and ("list" in tokens or "what" in tokens or "have" in tokens or "show" in tokens):
        if not subjects:
            return (
                "No subjects are saved yet.",
                ["subjects"],
                ["Import syllabus", "Add a subject"],
            )
        return (
            "Your subjects are:\n" + "\n".join(f"- {subject.name}" for subject in subjects),
            ["subjects"],
            [f"Show topics in {subjects[0].name}" if subjects else "Show my schedule"],
        )

    matched_subjects: list[Subject] = []
    for subject in subjects:
        aliases = _subject_aliases(subject.name)
        if any(alias in query for alias in aliases) or (_tokenize(subject.name) & tokens):
            matched_subjects.append(subject)

    if matched_subjects:
        subject = matched_subjects[0]
        ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
        if "topic" in tokens or "syllabus" in tokens or "unit" in tokens or "cover" in tokens:
            topic_lines = [f"- {topic.name}" for topic in ordered_topics[:25]]
            if len(ordered_topics) > 25:
                topic_lines.append(f"- ... and {len(ordered_topics) - 25} more topics")
            return (
                f"{subject.name} has {len(ordered_topics)} topics:\n" + "\n".join(topic_lines),
                [subject.name],
                [f"What should I study next in {subject.name}?", "Show my schedule"],
            )

        next_subject_sessions = [entry for entry in upcoming_entries if entry.subject_name == subject.name][:3]
        lines = [f"{subject.name} has {len(ordered_topics)} saved topics."]
        if next_subject_sessions:
            lines.append("Upcoming sessions:")
            lines.extend(f"- {_format_schedule_line(entry)}" for entry in next_subject_sessions)
        return "\n".join(lines), [subject.name, "schedule"], [f"Show topics in {subject.name}", "What should I study next?"]

    matched_topics: list[tuple[str, str]] = []
    for subject in subjects:
        for topic in subject.topics:
            topic_tokens = _tokenize(topic.name)
            if len(topic_tokens & tokens) >= 2 or _normalize(topic.name) in query:
                matched_topics.append((subject.name, topic.name))

    if matched_topics:
        grouped: dict[str, list[str]] = defaultdict(list)
        for subject_name, topic_name in matched_topics[:10]:
            grouped[subject_name].append(topic_name)
        lines = ["I found these matching topics:"]
        for subject_name, topic_names in grouped.items():
            lines.append(f"- {subject_name}: {', '.join(topic_names)}")
        return "\n".join(lines), ["topics"], ["Show my schedule", "List my subjects"]

    return (
        "I can use your saved study data for subjects, topics, and timetable. Ask about your schedule, subjects, or saved topics.",
        ["study_data"],
        ["List my subjects", "Show my schedule", "Show topics in a subject"],
    )


async def _llm_answer(
    message: str,
    subjects: list[Subject],
    upcoming_entries: list[ScheduleEntry],
    history: list[ChatMessage],
) -> tuple[str | None, str | None]:
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.groq_api_key:
        return None, None

    is_study_data_query = _is_study_data_query(message)
    subject_summaries = []
    if is_study_data_query:
        for subject in subjects[:5]:
            ordered_topics = sorted(subject.topics, key=lambda topic: (topic.order_index, topic.name))
            topic_preview = ", ".join(topic.name for topic in ordered_topics[:6])
            suffix = f", ... (+{len(ordered_topics) - 6} more)" if len(ordered_topics) > 6 else ""
            subject_summaries.append(f"{subject.name}: {topic_preview}{suffix}")

    schedule_preview = "\n".join(_format_schedule_line(entry) for entry in upcoming_entries[:5]) if is_study_data_query else ""
    system_prompt = (
        "You are an education-only assistant for a study planner app. "
        "Answer only educational questions. "
        "Be clear, accurate, and natural. "
        "For concept questions, give a definition, key points, and one example. "
        "For comparisons, give direct differences. "
        "For algorithms, give steps and time complexity. "
        "For code, provide working code when asked. "
        "If the question is outside education, briefly refuse and redirect to an educational topic. "
        "If the term is ambiguous or misspelled, ask for clarification instead of guessing. "
        "History, civics, political science, economics, constitution, government roles, and general-knowledge questions about public offices are educational and should be answered normally. "
        "For common computer-science abbreviations, prefer the standard academic meaning unless the user gives another domain. "
        "Examples: NLP = Natural Language Processing, RAG = Retrieval-Augmented Generation, DBMS = Database Management System, OS = Operating System."
    )
    base_messages = [{"role": "system", "content": system_prompt}]
    if is_study_data_query:
        context_prompt = (
            "Student context:\n"
            f"Subjects and topics:\n" + ("\n".join(subject_summaries) or "No subjects saved.") + "\n\n"
            f"Upcoming schedule:\n{schedule_preview or 'No upcoming schedule.'}"
        )
        base_messages.append({"role": "system", "content": context_prompt})
    single_topic_base_messages = list(base_messages)

    candidate_models: list[str] = []
    for model in [settings.groq_model, "llama-3.1-8b-instant"]:
        if model and model not in candidate_models:
            candidate_models.append(model)

    intent = _question_intent(message)
    multi_topic_explain = intent in {"explain", "types", "compare", "how"} and (
        message.count(",") >= 1 or len(re.findall(r"\b(and|vs|versus)\b", message.lower())) >= 1
    )
    extracted_topics = _extract_multi_topics(message) if multi_topic_explain else []
    wants_detailed_answer = _wants_detailed_answer(message)
    if multi_topic_explain:
        base_messages.append(
            {
                "role": "system",
                "content": (
                    "The user is asking about multiple topics in one question. "
                    "Cover every topic in one answer. "
                    "If the user did not explicitly ask for detail, keep each topic brief and balanced. "
                    "Do not over-expand the first topic and do not stop before covering all topics."
                ),
            }
        )
    if intent in {"explain", "types", "compare", "how", "exam_plan"}:
        max_tokens = 1200 if multi_topic_explain else 800
    elif intent == "code":
        max_tokens = 700
    else:
        max_tokens = 320
    history_sets = [
        history[-4:],
        history[-2:],
        [],
    ]

    async def _groq_complete(
        client: httpx.AsyncClient,
        model: str,
        messages: list[dict[str, str]],
        token_limit: int,
    ) -> tuple[str | None, str | None]:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": token_limit,
            },
        )
        response.raise_for_status()
        data = response.json()
        choice = data["choices"][0]
        content = (choice.get("message") or {}).get("content", "").strip()
        finish_reason = choice.get("finish_reason")
        return content or None, finish_reason

    if len(extracted_topics) >= 2:
        last_error = None
        for model in candidate_models:
            try:
                async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                    sections: list[str] = []
                    for topic in extracted_topics:
                        if wants_detailed_answer:
                            topic_prompt = (
                                f"Explain only {topic} as an algorithm. "
                                "Do not mention any other algorithm. "
                                "Use a concise exam-ready format with Definition, 2 or 3 Key points, one short Use case, and if useful one line on Steps or Time complexity. "
                                "Keep it under 170 words."
                            )
                            topic_token_limit = 240
                            continuation_limit = 80
                        else:
                            topic_prompt = (
                                f"Explain only {topic} as an algorithm. "
                                "Do not mention any other algorithm. "
                                "Do not add an introduction. "
                                "Use this exact brief format: "
                                "Definition: ... Key points: - ... - ... Use case: ... "
                                "Keep it under 90 words."
                            )
                            topic_token_limit = 130
                            continuation_limit = 40
                        topic_messages = list(single_topic_base_messages)
                        topic_messages.append(
                            {
                                "role": "user",
                                "content": topic_prompt,
                            }
                        )
                        content, finish_reason = await _groq_complete(client, model, topic_messages, topic_token_limit)
                        if content and finish_reason == "length":
                            followup_messages = list(topic_messages)
                            followup_messages.append({"role": "assistant", "content": content})
                            followup_messages.append(
                                {
                                    "role": "user",
                                    "content": "Finish the remaining line briefly without repeating anything.",
                                }
                            )
                            extra, _ = await _groq_complete(client, model, followup_messages, continuation_limit)
                            if extra:
                                content = _merge_continuation(content, extra)
                        if not content:
                            raise RuntimeError(f"Empty Groq answer for topic: {topic}")
                        sections.append(f"**{topic}**\n{content.strip()}")
                    if sections:
                        return "\n\n".join(sections), "groq" if model == settings.groq_model else "groq_backup"
            except Exception as exc:
                last_error = exc
                continue

    last_error = None
    for model in candidate_models:
        for short_history in history_sets:
            messages = list(base_messages)
            for item in short_history:
                messages.append({"role": item.role, "content": item.content[:1200]})
            messages.append({"role": "user", "content": message})

            for _ in range(2):
                try:
                    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                        content, finish_reason = await _groq_complete(client, model, messages, max_tokens)
                        continuation_count = 0
                        while content and (
                            finish_reason == "length" or _looks_truncated_answer(content)
                        ) and continuation_count < 2:
                            continuation_messages = list(messages)
                            continuation_messages.append({"role": "assistant", "content": content})
                            continuation_messages.append(
                                {
                                    "role": "user",
                                    "content": "Continue from exactly where you stopped. Do not repeat earlier lines or headings. Complete the remaining explanation briefly and end with a proper closing sentence.",
                                }
                            )
                            extra, finish_reason = await _groq_complete(client, model, continuation_messages, max_tokens // 2)
                            if not extra:
                                break
                            content = _merge_continuation(content, extra)
                            continuation_count += 1
                    if content:
                        return content, "groq" if model == settings.groq_model else "groq_backup"
                except Exception as exc:
                    last_error = exc
                    continue

    if last_error is not None:
        logger.warning("Groq chat generation failed for message=%r error=%s", message[:120], repr(last_error))

    return None, "groq_fallback"


@router.get("/history/{user_id}", response_model=list[ChatHistoryItem])
async def chat_history(user_id: str, db: AsyncSession = Depends(get_db)):
    """Return the recent chat log for a user."""
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
    get_settings.cache_clear()
    settings = get_settings()
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

    result = await db.execute(
        select(Subject)
        .where(Subject.user_id == payload.user_id)
        .options(selectinload(Subject.topics))
    )
    subjects = list(result.scalars().unique().all())

    schedule_result = await db.execute(
        select(ScheduleEntry)
        .where(ScheduleEntry.user_id == payload.user_id, ScheduleEntry.completed == 0)
        .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
    )
    upcoming_entries = list(schedule_result.scalars().all())

    ai_answer, provider = await _llm_answer(
        payload.message,
        subjects,
        upcoming_entries,
        payload.history,
    )
    if ai_answer:
        await _log_chat_message(db, payload.user_id, "assistant", ai_answer, provider or "llm")
        return ChatAskResponse(
            answer=ai_answer,
            sources=["subjects", "topics", "schedule"],
            suggestions=["What should I study next?", "Show topics in a subject", "How do I revise this unit?"],
            mode=provider or "llm",
        )

    groq_required_for_concepts = (
        settings.require_groq
        and bool(settings.groq_api_key)
        and _is_general_educational_query(payload.message)
        and not _is_study_data_query(payload.message)
    )
    if groq_required_for_concepts:
        answer, sources, suggestions = _llm_temporarily_unavailable_answer()
        mode = provider or "groq_unavailable"
        await _log_chat_message(db, payload.user_id, "assistant", answer, mode)
        return ChatAskResponse(
            answer=answer,
            sources=sources,
            suggestions=suggestions,
            mode=mode,
        )

    answer, sources, suggestions = _rule_based_answer(
        payload.message,
        subjects,
        upcoming_entries,
    )
    mode = "rule_based_fallback" if provider else "rule_based"

    await _log_chat_message(db, payload.user_id, "assistant", answer, mode)
    return ChatAskResponse(
        answer=answer,
        sources=sources,
        suggestions=suggestions,
        mode=mode,
    )
