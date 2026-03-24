"""Schedule API routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
import random
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.database import async_session_factory, ensure_db_ready, get_db
from backend.app.models.user import User
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.models.schedule import ScheduleEntry
from backend.app.schemas.schedule import (
    ScheduleGenerateRequest,
    ScheduleEntryOut,
    ScheduleFromSyllabusPdfOut,
    ScheduleRescheduleOut,
)
from backend.app.schemas.quiz import QuizGenerateRequest
from backend.app.schemas.progress import ProgressUpdate
from backend.app.services.progress import record_progress
from backend.app.services.scheduler import (
    generate_schedule,
    generate_schedule_rule_based,
    blocks_to_entries,
)
from backend.app.services.syllabus_langchain import (
    extract_unit_topics_from_pdf_with_langchain,
)
from backend.app.services.quiz_engine import create_quiz
from backend.app.services.syllabus_parser import parse_subjects_and_topics
from backend.app.services.robust_syllabus import (
    extract_pdf_text_robust,
    parse_subjects_and_topics_robust,
)
from backend.app.services.syllabus_ai import (
    extract_subjects_and_topics_with_llm,
    looks_too_sparse_for_schedule,
)
from backend.app.services.topic_text import (
    humanize_topic_text,
    looks_like_reference_text,
    split_period_topic_list,
    topic_dedupe_key,
)
from backend.app.services.adaptive_agent import AdaptiveAgent
from backend.app.api.syllabus import (
    _humanize_topic_text as _syllabus_humanize_topic_text,
    _is_strong_subject_name as _syllabus_is_strong_subject_name,
    _is_valid_topic_text as _syllabus_is_valid_topic_text,
    _normalize_topic_items as _syllabus_normalize_topic_items,
    _split_topic_candidates as _syllabus_split_topic_candidates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedule", tags=["schedule"])
_SCHEMA_RECOVERY_SENTINEL = "_schema_recovery_retried"

# Patterns and noise lists used throughout topic parsing/cleaning
_UNIT_WITH_SUFFIX_PATTERN = re.compile(
    r"^\s*(Unit\s*[IVXLC\d]+)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_TOPIC_SEP_PATTERN = re.compile(r"\s*(?:;|\||\u2022)\s*")
_COURSE_CODE_TOKEN_PATTERN = re.compile(r"\b[A-Z]{1,4}\d{3}[A-Z]{0,4}\b", re.IGNORECASE)
_COURSE_CODE_PREFIX_PATTERN = re.compile(r"^\s*[A-Z]{1,4}\d{3}[A-Z]{0,4}\s*[-:]\s*", re.IGNORECASE)
_BROKEN_COURSE_CODE_PATTERN = re.compile(r"\b[a-z]\s*\d{2,3}\s*[a-z](?:\s*[a-z])?\b", re.IGNORECASE)

_UNICODE_ROMAN_MAP = str.maketrans({
    "\u2160": "I",
    "\u2161": "II",
    "\u2162": "III",
    "\u2163": "IV",
    "\u2164": "V",
    "\u2165": "VI",
    "\u2166": "VII",
    "\u2167": "VIII",
    "\u2168": "IX",
    "\u2169": "X",
})
_UNIT_ONLY_PATTERN = re.compile(r"^\s*Unit\s*-?\s*([IVXLC\d]+)\s*$", re.IGNORECASE)
_UNIT_PREFIX_PATTERN = re.compile(r"^\s*Unit\s*-?\s*([IVXLC\d]+)\b", re.IGNORECASE)


def _humanize_topic_text(text: str) -> str:
    """Normalize OCR noise using the same cleaner as syllabus preview."""
    return _syllabus_humanize_topic_text(text)


def _split_period_topic_list(text: str) -> list[str]:
    return split_period_topic_list(text)


def _topic_dedupe_key(text: str) -> str:
    return topic_dedupe_key(text)


def _looks_like_reference_heading(text: str) -> bool:
    return looks_like_reference_text(text)


def _sanitize_schedule_entries(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    for entry in entries:
        entry.subject_name = _humanize_topic_text(entry.subject_name or "")
        entry.topic_name = _humanize_topic_text(entry.topic_name or "")
    return entries


def _expand_unit_only_topics(raw_topics: list[str]) -> list[str]:
    expanded: list[str] = []
    idx = 0
    while idx < len(raw_topics):
        current = _humanize_topic_text(raw_topics[idx])
        if not current:
            idx += 1
            continue
        unit_only = _UNIT_ONLY_PATTERN.match(current)
        if unit_only and idx + 1 < len(raw_topics):
            nxt = _humanize_topic_text(raw_topics[idx + 1])
            if nxt and not _UNIT_ONLY_PATTERN.match(nxt):
                expanded.append(f"Unit {unit_only.group(1)}: {nxt}")
                idx += 2
                continue
        expanded.append(current)
        idx += 1
    return expanded


def _split_topic_candidates(raw_topic: str) -> list[str]:
    return _syllabus_split_topic_candidates(raw_topic)


def _normalize_subject_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _strip_course_code_prefix(name: str) -> str:
    return _COURSE_CODE_PREFIX_PATTERN.sub("", _clean_subject_name(name)).strip()


def _subject_alias_key(name: str) -> str:
    return _normalize_subject_key(_strip_course_code_prefix(name))


def _clean_subject_name(name: str) -> str:
    cleaned = _humanize_topic_text(name)
    cleaned = cleaned.translate(_UNICODE_ROMAN_MAP)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def _resolved_subject_label(subject_name: str | None, filename: str | None) -> str:
    label = _clean_subject_name(subject_name or "")
    if label:
        return label
    stem = (filename or "").rsplit(".", 1)[0].strip()
    return _clean_subject_name(stem)


def _roman_to_int(token: str) -> int | None:
    t = token.strip().upper()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(t):
        score = values.get(ch)
        if score is None:
            return None
        if score < prev:
            total -= score
        else:
            total += score
            prev = score
    return total if total > 0 else None


def _topic_unit_number(name: str) -> int | None:
    match = _UNIT_PREFIX_PATTERN.match(_humanize_topic_text(name))
    return _roman_to_int(match.group(1)) if match else None


def _topic_body_text(name: str) -> str:
    text = _humanize_topic_text(name)
    return re.sub(r"^\s*Unit\s*[IVXLC\d]+\s*:\s*", "", text, flags=re.IGNORECASE).strip()


def _topic_body_key(name: str) -> str:
    return _topic_dedupe_key(_topic_body_text(name))


def _normalize_topic_items(raw_topics: list[str], subject_name: str) -> list[str]:
    return _syllabus_normalize_topic_items(raw_topics, subject_name)


def _is_strong_subject_name(name: str) -> bool:
    return _syllabus_is_strong_subject_name(name)


def _is_valid_topic_text(topic_name: str, subject_name: str | None = None) -> bool:
    return _syllabus_is_valid_topic_text(topic_name, subject_name)


def _is_schedule_safe_topic(
    topic_name: str,
    *,
    subject_name: str | None,
    unit_start: int,
    unit_end: int,
) -> bool:
    text = _humanize_topic_text(topic_name)
    if not text or not _is_valid_topic_text(text, subject_name):
        return False
    unit_no = _topic_unit_number(text)
    if unit_no is None:
        return False
    if unit_no < unit_start or unit_no > unit_end:
        return False
    body = _topic_body_text(text)
    if not body or _looks_like_reference_heading(body):
        return False
    if re.search(r"\b(?:mcgraw|mc graw|wiley|pearson|oxford|cambridge|edition|publishers?)\b", body, re.IGNORECASE):
        return False
    return True


def _filter_schedule_topics(
    topics: list[str],
    *,
    subject_name: str | None,
    unit_start: int,
    unit_end: int,
    max_topics: int,
) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for topic_name in topics:
        cleaned = _humanize_topic_text(topic_name)
        if not _is_schedule_safe_topic(
            cleaned,
            subject_name=subject_name,
            unit_start=unit_start,
            unit_end=unit_end,
        ):
            continue
        topic_key = _topic_dedupe_key(cleaned)
        if topic_key in seen:
            continue
        seen.add(topic_key)
        filtered.append(cleaned[:300])
        if len(filtered) >= max_topics:
            break
    return filtered


def _merge_subject_topic_sources(
    robust_subjects: dict[str, list[str]],
    parser_subjects: list[dict[str, object]],
    *,
    max_topics_per_subject: int,
    allow_new_subjects_when_merged: bool = False,
) -> list[dict[str, list[str]]]:
    merged: dict[str, list[str]] = {
        _clean_subject_name(name): _normalize_topic_items(list(topics), _clean_subject_name(name))
        for name, topics in robust_subjects.items()
        if _clean_subject_name(name) and _is_strong_subject_name(name)
    }
    key_to_name = {_normalize_subject_key(name): name for name in merged.keys()}
    alias_to_name = {_subject_alias_key(name): name for name in merged.keys() if _subject_alias_key(name)}

    for subject in parser_subjects:
        raw_name = _clean_subject_name(subject.get("name") or "")
        if not raw_name:
            continue
        raw_topics = [str(topic or "").strip() for topic in list(subject.get("topics") or [])]
        expanded_topics = _filter_schedule_topics(
            _normalize_topic_items(raw_topics, raw_name),
            subject_name=raw_name,
            unit_start=1,
            unit_end=12,
            max_topics=max_topics_per_subject,
        )
        if not expanded_topics:
            continue

        key = _normalize_subject_key(raw_name)
        alias_key = _subject_alias_key(raw_name)
        target_name = key_to_name.get(key) or alias_to_name.get(alias_key)
        if target_name is None:
            # Classic parser stays conservative when robust parser already found subjects.
            if merged and not allow_new_subjects_when_merged:
                continue
            if not _is_strong_subject_name(raw_name):
                continue
            merged[raw_name] = []
            key_to_name[key] = raw_name
            if alias_key:
                alias_to_name[alias_key] = raw_name
            target_name = raw_name

        seen = {_topic_dedupe_key(topic) for topic in merged[target_name]}
        for topic in expanded_topics:
            if not _is_valid_topic_text(topic, target_name):
                continue
            topic_key = _topic_dedupe_key(topic)
            if topic_key in seen:
                continue
            seen.add(topic_key)
            merged[target_name].append(topic)
            if len(merged[target_name]) >= max_topics_per_subject:
                break

    return [{"name": name, "topics": topics} for name, topics in merged.items()]


def _normalize_ai_subjects(
    ai_subjects: list[dict[str, object]],
    *,
    max_topics_per_subject: int,
) -> list[dict[str, list[str]]]:
    normalized: list[dict[str, list[str]]] = []
    for subject in ai_subjects:
        raw_name = _clean_subject_name(subject.get("name") or "")
        if not raw_name or not _is_strong_subject_name(raw_name):
            continue

        normalized_topics = _normalize_topic_items(
            [str(topic or "") for topic in list(subject.get("topics") or [])],
            raw_name,
        )
        normalized_topics = _filter_schedule_topics(
            normalized_topics,
            subject_name=raw_name,
            unit_start=1,
            unit_end=12,
            max_topics=max_topics_per_subject,
        )
        if not normalized_topics:
            continue

        normalized.append(
            {
                "name": raw_name,
                "topics": normalized_topics[:max_topics_per_subject],
            }
        )
    return normalized


def _merge_normalized_subject_lists(
    primary_subjects: list[dict[str, object]],
    secondary_subjects: list[dict[str, object]],
    *,
    max_topics_per_subject: int,
) -> list[dict[str, list[str]]]:
    merged: dict[str, list[str]] = {}
    key_to_name: dict[str, str] = {}
    alias_to_name: dict[str, str] = {}

    def upsert(subjects: list[dict[str, object]]) -> None:
        for subject in subjects:
            raw_name = _clean_subject_name(subject.get("name") or "")
            if not raw_name or not _is_strong_subject_name(raw_name):
                continue
            normalized_topics = _normalize_topic_items(
                [str(topic or "") for topic in list(subject.get("topics") or [])],
                raw_name,
            )
            normalized_topics = _filter_schedule_topics(
                normalized_topics,
                subject_name=raw_name,
                unit_start=1,
                unit_end=12,
                max_topics=max_topics_per_subject,
            )
            if not normalized_topics:
                continue

            key = _normalize_subject_key(raw_name)
            alias_key = _subject_alias_key(raw_name)
            target_name = key_to_name.get(key) or alias_to_name.get(alias_key) or raw_name
            if target_name not in merged:
                merged[target_name] = []
                key_to_name[key] = target_name
                if alias_key:
                    alias_to_name[alias_key] = target_name

            seen = {_topic_dedupe_key(topic) for topic in merged[target_name]}
            for topic in normalized_topics:
                topic_key = _topic_dedupe_key(topic)
                if topic_key in seen:
                    continue
                seen.add(topic_key)
                merged[target_name].append(topic)
                if len(merged[target_name]) >= max_topics_per_subject:
                    break

    upsert(primary_subjects)
    upsert(secondary_subjects)

    return [{"name": name, "topics": topics} for name, topics in merged.items()]


def _should_prefer_classic_subjects(
    robust_subjects: dict[str, list[str]],
    parser_subjects: list[dict[str, object]],
) -> bool:
    if not parser_subjects:
        return False
    classic_with_codes = [
        item for item in parser_subjects if _COURSE_CODE_TOKEN_PATTERN.search(str(item.get("name") or ""))
    ]
    if not classic_with_codes:
        return False
    robust_count = sum(len(topics) for topics in robust_subjects.values())
    classic_count = sum(len(list(item.get("topics") or [])) for item in parser_subjects)
    if robust_count == 0:
        return True
    return robust_count >= max(classic_count * 2, classic_count + 20)


def _collect_units_from_topic_names(topic_names: list[str]) -> set[int]:
    units: set[int] = set()
    for topic_name in topic_names:
        unit_no = _topic_unit_number(topic_name)
        if unit_no is not None:
            units.add(unit_no)
    return units


def _should_prefer_unit_extractor(
    *,
    topics_by_unit: dict[int, list[str]],
    parsed_subjects: list[dict[str, object]],
    unit_start: int,
    unit_end: int,
) -> bool:
    if not topics_by_unit:
        return False

    requested_units = {unit for unit in range(unit_start, unit_end + 1)}
    extracted_units = {unit for unit in topics_by_unit if unit in requested_units and topics_by_unit[unit]}
    if not extracted_units:
        return False

    parsed_topic_names = [
        str(topic or "")
        for subject in parsed_subjects
        for topic in list(subject.get("topics") or [])
    ]
    parsed_units = {
        unit for unit in _collect_units_from_topic_names(parsed_topic_names) if unit in requested_units
    }
    extracted_topic_count = sum(len(topics_by_unit.get(unit, [])) for unit in extracted_units)
    parsed_topic_count = len(parsed_topic_names)

    if not parsed_subjects:
        return True
    if len(parsed_subjects) > 1:
        return False
    if len(extracted_units) > len(parsed_units):
        return True
    if extracted_units == requested_units and extracted_topic_count >= parsed_topic_count:
        return True
    if len(extracted_units) >= max(2, len(requested_units) - 1) and extracted_topic_count >= parsed_topic_count + 3:
        return True
    return False

def _build_revision_entries_between(
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    session_duration_mins: int,
    topics: list[Topic],
    subject_name_by_id: dict[str, str],
    study_entries: list[ScheduleEntry] | None = None,
) -> list[ScheduleEntry]:
    total_days = (end_date - start_date).days + 1
    if total_days < 4 or not topics:
        return []

    candidate_days = [
        start_date + timedelta(days=offset)
        for offset in range(6, total_days - 1, 7)
    ]
    if not candidate_days:
        candidate_days = [start_date + timedelta(days=max(1, total_days // 2))]

    topics_by_id = {topic.id: topic for topic in topics}
    ordered_review: list[tuple[Topic, int, date]] = []
    seen_units: set[tuple[str, int]] = set()
    if study_entries:
        sorted_entries = sorted(
            (entry for entry in study_entries if not entry.is_revision),
            key=lambda entry: (entry.scheduled_date, entry.start_time),
        )
        first_seen_by_unit: dict[tuple[str, int], tuple[Topic, date]] = {}
        for entry in sorted_entries:
            topic = topics_by_id.get(entry.topic_id)
            if not topic:
                continue
            unit_no = _topic_unit_number(topic.name)
            if unit_no is None:
                continue
            unit_key = (topic.subject_id, unit_no)
            if unit_key in first_seen_by_unit:
                continue
            first_seen_by_unit[unit_key] = (topic, entry.scheduled_date)
        for (subject_id, unit_no), (topic, first_day) in sorted(
            first_seen_by_unit.items(),
            key=lambda item: item[1][1],
        ):
            unit_key = (subject_id, unit_no)
            if unit_key in seen_units:
                continue
            seen_units.add(unit_key)
            ordered_review.append((topic, unit_no, first_day))
    else:
        review_topics = topics[:]
        random.shuffle(review_topics)
        for topic in review_topics:
            unit_no = _topic_unit_number(topic.name)
            if unit_no is None:
                continue
            unit_key = (topic.subject_id, unit_no)
            if unit_key in seen_units:
                continue
            seen_units.add(unit_key)
            ordered_review.append((topic, unit_no, start_date))

    slots = min(len(ordered_review), len(candidate_days))
    revision_entries: list[ScheduleEntry] = []
    topic_idx = 0
    for target_day in candidate_days:
        while topic_idx < len(ordered_review) and ordered_review[topic_idx][2] >= target_day:
            topic_idx += 1
        if topic_idx >= len(ordered_review):
            break
        topic, unit_no, _ = ordered_review[topic_idx]
        topic_idx += 1
        start_dt = datetime.combine(target_day, time(18, 0))
        end_dt = start_dt + timedelta(minutes=session_duration_mins)
        revision_entries.append(
            ScheduleEntry(
                user_id=user_id,
                topic_id=topic.id,
                subject_name=subject_name_by_id.get(topic.subject_id, "General"),
                topic_name=f"Revision: Unit {unit_no}",
                scheduled_date=target_day,
                start_time=start_dt.time(),
                end_time=end_dt.time(),
                duration_mins=session_duration_mins,
                priority_score=1.25,
                is_revision=1,
                completed=0,
            )
        )
        if len(revision_entries) >= slots:
            break

    return revision_entries


@router.post("/generate", response_model=list[ScheduleEntryOut], status_code=201)
async def generate(payload: ScheduleGenerateRequest, db: AsyncSession = Depends(get_db)):
    """Generate (or regenerate) a study schedule for the date range."""
    try:
        user = await db.get(User, payload.user_id)
        if not user:
            raise HTTPException(404, "User not found")

        # Load subjects with topics
        result = await db.execute(
            select(Subject)
            .where(Subject.user_id == payload.user_id)
            .options(selectinload(Subject.topics))
        )
        subjects = list(result.scalars().unique().all())
        if not subjects:
            raise HTTPException(400, "No subjects found - add subjects and topics first.")

        if payload.no_ai_mode:
            blocks = generate_schedule_rule_based(
                user_id=payload.user_id,
                subjects=subjects,
                start_date=payload.start_date,
                end_date=payload.end_date,
                daily_hours=payload.daily_study_hours or user.daily_study_hours,
                daily_start=payload.daily_start_time,
                session_mins=payload.session_duration_mins,
                break_mins=payload.break_duration_mins,
                max_topics_per_day=payload.max_topics_per_day,
                distribute_across_range=True,
                ensure_full_coverage=False,
            )
        else:
            blocks = generate_schedule(
                user_id=payload.user_id,
                subjects=subjects,
                start_date=payload.start_date,
                end_date=payload.end_date,
                daily_hours=payload.daily_study_hours or user.daily_study_hours,
                daily_start=payload.daily_start_time,
                session_mins=payload.session_duration_mins,
                break_mins=payload.break_duration_mins,
                max_topics_per_day=payload.max_topics_per_day,
                avoid_topic_repeats=True,
                enforce_unit_sequence=True,
                distribute_across_range=True,
                ensure_full_coverage=False,
            )

        await db.execute(
            delete(ScheduleEntry).where(
                ScheduleEntry.user_id == payload.user_id,
                ScheduleEntry.scheduled_date >= payload.start_date,
            )
        )
        entries = blocks_to_entries(payload.user_id, blocks)
        db.add_all(entries)
        await db.flush()

        generated_end_date = max(
            (entry.scheduled_date for entry in entries),
            default=payload.end_date,
        )
        q = (
            select(ScheduleEntry)
            .where(
                ScheduleEntry.user_id == payload.user_id,
                ScheduleEntry.scheduled_date >= payload.start_date,
                ScheduleEntry.scheduled_date <= generated_end_date,
            )
            .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
        )
        reloaded = await db.execute(q)
        return _sanitize_schedule_entries(list(reloaded.scalars().all()))
    except OperationalError as exc:
        await db.rollback()
        if "no such table" in str(exc).lower():
            if getattr(db, _SCHEMA_RECOVERY_SENTINEL, False):
                raise HTTPException(
                    503,
                    "Database schema recovery was attempted, but schedule generation still failed.",
                ) from exc
            await ensure_db_ready()
            async with async_session_factory() as recovered_db:
                setattr(recovered_db, _SCHEMA_RECOVERY_SENTINEL, True)
                result = await generate(payload, db=recovered_db)
                await recovered_db.commit()
                return result
        raise HTTPException(400, f"Database error while generating schedule: {exc}") from exc


@router.post(
    "/generate-from-syllabus-pdf",
    response_model=ScheduleFromSyllabusPdfOut,
    status_code=201,
)
async def generate_from_syllabus_pdf(
    user_id: str = Form(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
    subject_name: str | None = Form(None),
    exam_date: date | None = Form(None),
    daily_start_time: time = Form(time(8, 0)),
    daily_study_hours: float | None = Form(None),
    session_duration_mins: int = Form(60),
    break_duration_mins: int = Form(15),
    default_topic_hours: float = Form(1.0),
    default_topic_difficulty: float = Form(0.5),
    unit_start: int = Form(1),
    unit_end: int = Form(5),
    max_topics_per_unit: int = Form(120),
    max_topics_per_day: int = Form(5),
    include_revisions: bool = Form(True),
    revision_days: int = Form(3),
    auto_generate_quizzes: bool = Form(True),
    quiz_difficulty: str = Form("medium"),
    quiz_questions: int = Form(5),
    no_ai_mode: bool = Form(False),
    import_all_subjects: bool = Form(True),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload syllabus PDF, extract Unit topics via LangChain chunking, and generate a timetable.
    """
    try:
        await ensure_db_ready()
        if start_date > end_date:
            raise HTTPException(400, "start_date must be on or before end_date")
        if unit_start < 1 or unit_end < unit_start:
            raise HTTPException(400, "Invalid unit range")
        if revision_days < 1 or revision_days > 14:
            raise HTTPException(400, "revision_days must be between 1 and 14")
        if max_topics_per_unit < 5 or max_topics_per_unit > 200:
            raise HTTPException(400, "max_topics_per_unit must be between 5 and 200")
        if max_topics_per_day < 1 or max_topics_per_day > 12:
            raise HTTPException(400, "max_topics_per_day must be between 1 and 12")
        if quiz_difficulty not in {"easy", "medium", "hard"}:
            raise HTTPException(400, "quiz_difficulty must be easy, medium, or hard")
        if quiz_questions < 1 or quiz_questions > 20:
            raise HTTPException(400, "quiz_questions must be between 1 and 20")

        user = await db.get(User, user_id)
        if not user:
            raise HTTPException(404, "User not found")

        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Please upload a PDF file")

        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(400, "Uploaded PDF is empty")

        topics_by_unit: dict[int, list[str]] = {}
        topics_created_records: list[Topic] = []
        created_subject_ids: list[str] = []
        created_subject_names: list[str] = []
        topic_index = 0
        imported_topic_hours = max(
            0.25,
            min(default_topic_hours, max(session_duration_mins, 1) / 60.0),
        )
        pdf_subject_label = _resolved_subject_label(subject_name, file.filename)

        try:
            topics_by_unit = extract_unit_topics_from_pdf_with_langchain(
                raw_bytes,
                unit_start=unit_start,
                unit_end=unit_end,
                max_topics_per_unit=max_topics_per_unit,
            )
        except RuntimeError:
            topics_by_unit = {}
        except Exception as exc:
            logger.warning("Unit extractor failed during schedule import precheck: %s", exc)
            topics_by_unit = {}

        if import_all_subjects:
            # Start from OCR-cleaned text and let the configured LLM extract only the requested units.
            text = extract_pdf_text_robust(raw_bytes)
            parsed_subjects = []
            robust_subjects: dict[str, list[str]] = {}
            parser_subjects: list[dict[str, object]] = []
            if not no_ai_mode:
                llm_subjects = await extract_subjects_and_topics_with_llm(
                    text,
                    unit_start=unit_start,
                    unit_end=unit_end,
                )
                if not looks_too_sparse_for_schedule(
                    llm_subjects,
                    unit_start=unit_start,
                    unit_end=unit_end,
                ):
                    parsed_subjects = _normalize_ai_subjects(
                        llm_subjects,
                        max_topics_per_subject=max_topics_per_unit,
                    )

            robust_subjects = parse_subjects_and_topics_robust(
                text,
                unit_start=unit_start,
                unit_end=unit_end,
                max_topics_per_subject=max_topics_per_unit,
            )
            if not no_ai_mode:
                parser_subjects = parse_subjects_and_topics(text)

            # Only fall back to rule-based parsing if AI extraction produced nothing usable.
            if not parsed_subjects:
                if no_ai_mode:
                    parsed_subjects = [
                        {
                            "name": _clean_subject_name(name),
                            "topics": _normalize_topic_items(topics, _clean_subject_name(name)),
                        }
                        for name, topics in robust_subjects.items()
                        if (
                            _clean_subject_name(name)
                            and _is_strong_subject_name(_clean_subject_name(name))
                            and _normalize_topic_items(topics, _clean_subject_name(name))
                        )
                    ]
                else:
                    if _should_prefer_classic_subjects(robust_subjects, parser_subjects):
                        parsed_subjects = parser_subjects
                    else:
                        parsed_subjects = _merge_subject_topic_sources(
                            robust_subjects,
                            parser_subjects,
                            max_topics_per_subject=max_topics_per_unit,
                        )
            elif not no_ai_mode:
                supplemental_subjects = (
                    parser_subjects
                    if _should_prefer_classic_subjects(robust_subjects, parser_subjects)
                    else _merge_subject_topic_sources(
                        robust_subjects,
                        parser_subjects,
                        max_topics_per_subject=max_topics_per_unit,
                        allow_new_subjects_when_merged=True,
                    )
                )
                parsed_subjects = _merge_normalized_subject_lists(
                    parsed_subjects,
                    supplemental_subjects,
                    max_topics_per_subject=max_topics_per_unit,
                )
            if _should_prefer_unit_extractor(
                topics_by_unit=topics_by_unit,
                parsed_subjects=parsed_subjects,
                unit_start=unit_start,
                unit_end=unit_end,
            ):
                parsed_subjects = []
            if parsed_subjects:
                for parsed_idx, parsed_subject in enumerate(parsed_subjects):
                    parsed_name = _clean_subject_name(parsed_subject.get("name") or "")
                    if not parsed_name:
                        if len(parsed_subjects) == 1 and pdf_subject_label:
                            parsed_name = pdf_subject_label
                        else:
                            continue
                    subject = Subject(
                        user_id=user_id,
                        name=parsed_name[:200],
                        exam_date=exam_date,
                        priority=3.0,
                        color="#4A90D9",
                    )
                    db.add(subject)
                    await db.flush()
                    created_subject_ids.append(subject.id)
                    created_subject_names.append(subject.name)

                    seen_topics: set[str] = set()
                    per_subject_count = 0
                    topic_candidates = _filter_schedule_topics(
                        _expand_unit_only_topics([str(topic) for topic in parsed_subject.get("topics", [])]),
                        subject_name=parsed_name,
                        unit_start=unit_start,
                        unit_end=unit_end,
                        max_topics=max_topics_per_unit,
                    )
                    for raw_topic in topic_candidates:
                        for topic_clean in _split_topic_candidates(raw_topic):
                            if not _is_schedule_safe_topic(
                                topic_clean,
                                subject_name=parsed_name,
                                unit_start=unit_start,
                                unit_end=unit_end,
                            ):
                                continue
                            topic_key = _topic_dedupe_key(topic_clean)
                            if topic_key in seen_topics:
                                continue
                            seen_topics.add(topic_key)
                            topic_record = Topic(
                                subject_id=subject.id,
                                name=topic_clean[:300],
                                difficulty=min(max(default_topic_difficulty, 0.0), 1.0),
                                estimated_hours=imported_topic_hours,
                                order_index=topic_index,
                            )
                            db.add(topic_record)
                            topics_created_records.append(topic_record)
                            topic_index += 1
                            per_subject_count += 1
                            if per_subject_count >= max_topics_per_unit:
                                break
                        if per_subject_count >= max_topics_per_unit:
                            break

                await db.flush()

        # Fallback to Unit-based import when subject parser doesn't yield usable data.
        if not topics_created_records:
            if not topics_by_unit:
                try:
                    topics_by_unit = extract_unit_topics_from_pdf_with_langchain(
                        raw_bytes,
                        unit_start=unit_start,
                        unit_end=unit_end,
                        max_topics_per_unit=max_topics_per_unit,
                    )
                except RuntimeError as exc:
                    # Missing optional deps (e.g., langchain-text-splitters) or similar runtime guards.
                    # Surface as a client error so the UI can prompt for installing extras instead of 500.
                    raise HTTPException(400, str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(400, f"Could not parse syllabus PDF: {exc}") from exc

            if not topics_by_unit:
                text = extract_pdf_text_robust(raw_bytes)
                robust_subjects = parse_subjects_and_topics_robust(
                    text,
                    unit_start=1,
                    unit_end=max(unit_end, 12),
                    max_topics_per_subject=max_topics_per_unit,
                )
                flattened_topics: list[str] = []
                seen_flattened: set[str] = set()
                for robust_name, robust_topics in robust_subjects.items():
                    normalized = _filter_schedule_topics(
                        _normalize_topic_items(list(robust_topics), robust_name),
                        subject_name=robust_name,
                        unit_start=unit_start,
                        unit_end=unit_end,
                        max_topics=max_topics_per_unit,
                    )
                    for topic in normalized:
                        topic_key = _topic_dedupe_key(topic)
                        if topic_key in seen_flattened:
                            continue
                        seen_flattened.add(topic_key)
                        flattened_topics.append(topic)
                        unit_no = _topic_unit_number(topic)
                        if unit_no is not None:
                            topics_by_unit.setdefault(unit_no, []).append(_topic_body_text(topic))

                if not flattened_topics:
                    raise HTTPException(
                        400,
                        "Could not extract any real syllabus topics from this PDF. Please try a clearer PDF or adjust the unit range.",
                    )

                if not pdf_subject_label and len(robust_subjects) == 1:
                    pdf_subject_label = _clean_subject_name(next(iter(robust_subjects.keys())))
                if not pdf_subject_label:
                    raise HTTPException(
                        400,
                        "Could not determine a subject name from the PDF. Please rename the PDF clearly or provide subject_name.",
                    )
            else:
                flattened_topics = []

            label = pdf_subject_label
            if not label:
                raise HTTPException(
                    400,
                    "Could not determine a subject name from the PDF. Please rename the PDF clearly or provide subject_name.",
                )

            subject = Subject(
                user_id=user_id,
                name=label[:200],
                exam_date=exam_date,
                priority=3.0,
                color="#4A90D9",
            )
            db.add(subject)
            await db.flush()
            created_subject_ids.append(subject.id)
            created_subject_names.append(subject.name)

            if topics_by_unit:
                for unit in sorted(topics_by_unit.keys()):
                    if unit < unit_start or unit > unit_end:
                        continue
                    for topic in topics_by_unit[unit]:
                        raw_topic_name = f"Unit {unit}: {topic}".strip()
                        topic_names = _split_topic_candidates(raw_topic_name)
                        if not topic_names:
                            fallback_topic_name = _humanize_topic_text(raw_topic_name)
                            if fallback_topic_name:
                                topic_names = [fallback_topic_name]
                        for topic_name in topic_names:
                            if not _is_schedule_safe_topic(
                                topic_name,
                                subject_name=label,
                                unit_start=unit_start,
                                unit_end=unit_end,
                            ):
                                continue
                            topic_record = Topic(
                                subject_id=subject.id,
                                name=topic_name[:300],
                                difficulty=min(max(default_topic_difficulty, 0.0), 1.0),
                                estimated_hours=imported_topic_hours,
                                order_index=topic_index,
                            )
                            db.add(topic_record)
                            topics_created_records.append(topic_record)
                            topic_index += 1
            else:
                for topic_name in flattened_topics:
                    if not _is_schedule_safe_topic(
                        topic_name,
                        subject_name=label,
                        unit_start=unit_start,
                        unit_end=unit_end,
                    ):
                        continue
                    topic_record = Topic(
                        subject_id=subject.id,
                        name=topic_name[:300],
                        difficulty=min(max(default_topic_difficulty, 0.0), 1.0),
                        estimated_hours=imported_topic_hours,
                        order_index=topic_index,
                    )
                    db.add(topic_record)
                    topics_created_records.append(topic_record)
                    topic_index += 1

            await db.flush()

        subject_scope = (
            select(Subject).where(Subject.id.in_(created_subject_ids))
            if created_subject_ids
            else select(Subject).where(Subject.user_id == user_id)
        )
        all_subjects_result = await db.execute(
            subject_scope.options(selectinload(Subject.topics))
        )
        all_subjects = list(all_subjects_result.scalars().unique().all())
        if not all_subjects:
            raise HTTPException(400, "No subjects found for schedule generation")
        if created_subject_ids:
            created_order = {subject_id: idx for idx, subject_id in enumerate(created_subject_ids)}
            all_subjects.sort(key=lambda subject: created_order.get(subject.id, 10**9))
        subject_name_by_id = {subject.id: subject.name for subject in all_subjects}

        if no_ai_mode:
            blocks = generate_schedule_rule_based(
                user_id=user_id,
                subjects=all_subjects,
                start_date=start_date,
                end_date=end_date,
                daily_hours=daily_study_hours or user.daily_study_hours,
                daily_start=daily_start_time,
                session_mins=session_duration_mins,
                break_mins=break_duration_mins,
                max_topics_per_day=max_topics_per_day,
                distribute_across_range=False,
                ensure_full_coverage=True,
            )
        else:
            blocks = generate_schedule(
                user_id=user_id,
                subjects=all_subjects,
                start_date=start_date,
                end_date=end_date,
                daily_hours=daily_study_hours or user.daily_study_hours,
                daily_start=daily_start_time,
                session_mins=session_duration_mins,
                break_mins=break_duration_mins,
                max_topics_per_day=max_topics_per_day,
                avoid_topic_repeats=True,
                enforce_unit_sequence=True,
                distribute_across_range=False,
                ensure_full_coverage=True,
            )

        await db.execute(
            delete(ScheduleEntry).where(
                ScheduleEntry.user_id == user_id,
                ScheduleEntry.scheduled_date >= start_date,
            )
        )
        entries = blocks_to_entries(user_id, blocks)
        db.add_all(entries)
        await db.flush()

        revision_entries_added = 0
        if include_revisions and entries:
            revision_entries = _build_revision_entries_between(
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                session_duration_mins=session_duration_mins,
                topics=topics_created_records,
                subject_name_by_id=subject_name_by_id,
                study_entries=entries,
            )
            if revision_entries:
                db.add_all(revision_entries)
                await db.flush()
                revision_entries_added = len(revision_entries)

        quizzes_generated = 0
        if auto_generate_quizzes and topics_created_records:
            topics_by_unit_bucket: dict[int, list[Topic]] = {}
            for topic in topics_created_records:
                unit_no = _topic_unit_number(topic.name)
                if unit_no is None or unit_no < unit_start or unit_no > unit_end:
                    unit_no = unit_start
                topics_by_unit_bucket.setdefault(unit_no, []).append(topic)

            for unit_no in sorted(topics_by_unit_bucket.keys()):
                selected_topic = topics_by_unit_bucket[unit_no][0]
                try:
                    await create_quiz(
                        db,
                        QuizGenerateRequest(
                            user_id=user_id,
                            topic_id=selected_topic.id,
                            difficulty=quiz_difficulty,
                            num_questions=quiz_questions,
                        ),
                    )
                    quizzes_generated += 1
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.warning("Auto quiz generation failed for unit %s: %s", unit_no, exc)
                    continue

            await db.flush()

        generated_result = await db.execute(
            select(ScheduleEntry)
            .where(
                ScheduleEntry.user_id == user_id,
                ScheduleEntry.scheduled_date >= start_date,
            )
            .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
        )
        generated_entries = list(generated_result.scalars().all())
        coverage_end_date = (
            max((entry.scheduled_date for entry in generated_entries), default=None)
            if generated_entries
            else None
        )

        subject_name_out = (
            created_subject_names[0]
            if len(created_subject_names) == 1
            else f"Imported {len(created_subject_names)} subjects"
        )
        units_detected_out = sorted(topics_by_unit.keys()) if topics_by_unit else sorted(
            {
                unit_no
                for topic in topics_created_records
                for unit_no in [_topic_unit_number(topic.name)]
                if unit_no is not None and unit_start <= unit_no <= unit_end
            }
        )

        return {
            "subject_id": created_subject_ids[0] if created_subject_ids else "",
            "subject_name": subject_name_out,
            "unit_range": f"Unit {unit_start} to Unit {unit_end}",
            "units_detected": units_detected_out,
            "topics_created": topic_index,
            "revision_entries_added": revision_entries_added,
            "quizzes_generated": quizzes_generated,
            "coverage_end_date": coverage_end_date,
            "schedule_entries": _sanitize_schedule_entries(generated_entries),
        }
    except OperationalError as exc:
        await db.rollback()
        if "no such table" in str(exc).lower():
            if getattr(db, _SCHEMA_RECOVERY_SENTINEL, False):
                raise HTTPException(
                    503,
                    "Database schema recovery was attempted, but PDF schedule generation still failed.",
                ) from exc
            await ensure_db_ready()
            async with async_session_factory() as recovered_db:
                setattr(recovered_db, _SCHEMA_RECOVERY_SENTINEL, True)
                result = await generate_from_syllabus_pdf(
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    subject_name=subject_name,
                    exam_date=exam_date,
                    daily_start_time=daily_start_time,
                    daily_study_hours=daily_study_hours,
                    session_duration_mins=session_duration_mins,
                    break_duration_mins=break_duration_mins,
                    default_topic_hours=default_topic_hours,
                    default_topic_difficulty=default_topic_difficulty,
                    unit_start=unit_start,
                    unit_end=unit_end,
                    max_topics_per_unit=max_topics_per_unit,
                    max_topics_per_day=max_topics_per_day,
                    include_revisions=include_revisions,
                    revision_days=revision_days,
                    auto_generate_quizzes=auto_generate_quizzes,
                    quiz_difficulty=quiz_difficulty,
                    quiz_questions=quiz_questions,
                    no_ai_mode=no_ai_mode,
                    import_all_subjects=import_all_subjects,
                    file=file,
                    db=recovered_db,
                )
                await recovered_db.commit()
                return result
        raise HTTPException(400, f"Database error while generating schedule: {exc}") from exc
    except HTTPException:
        # Let FastAPI return the intended status/response.
        raise
    except Exception as exc:  # pragma: no cover - last-resort guard
        logger.exception("generate-from-syllabus-pdf failed: %s", exc)
        # Return 400 so the client gets a useful message instead of a generic 500.
        raise HTTPException(
            400,
            f"Could not generate schedule from PDF: {exc}",
        ) from exc


@router.get("/{user_id}", response_model=list[ScheduleEntryOut])
async def get_schedule(user_id: str, db: AsyncSession = Depends(get_db)):
    """Get the full schedule for a user."""
    today = date.today()
    overdue_q = (
        select(ScheduleEntry.id)
        .where(
            ScheduleEntry.user_id == user_id,
            ScheduleEntry.completed == 0,
            ScheduleEntry.scheduled_date < today,
        )
        .limit(1)
    )
    overdue_result = await db.execute(overdue_q)
    if overdue_result.scalar_one_or_none():
        await AdaptiveAgent(db, user_id).run()

    q = (
        select(ScheduleEntry)
        .where(ScheduleEntry.user_id == user_id)
        .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
    )
    result = await db.execute(q)
    return _sanitize_schedule_entries(list(result.scalars().all()))


@router.patch("/complete/{entry_id}")
async def complete_entry(entry_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a schedule entry as completed."""
    entry = await db.get(ScheduleEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Schedule entry not found")
    entry.completed = 1
    # Log progress for the topic if present
    if entry.topic_id:
        topic = await db.get(Topic, entry.topic_id)
        if topic:
            session_mins = entry.duration_mins or 60
            # Mark topic as fully completed when the scheduled study session is done
            new_completion = 100.0
            topic.completed = 1
            await record_progress(
                db,
                ProgressUpdate(
                    user_id=entry.user_id,
                    topic_id=entry.topic_id,
                    completion_pct=new_completion,
                    time_spent_mins=session_mins,
                    notes="Session completed via schedule",
                ),
            )
    await db.flush()
    return {"status": "completed", "entry_id": entry_id}


@router.patch("/unread/{entry_id}")
async def unread_entry(entry_id: str, db: AsyncSession = Depends(get_db)):
    """Mark a schedule entry as unread again."""
    entry = await db.get(ScheduleEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Schedule entry not found")

    entry.completed = 0
    if entry.topic_id:
        topic = await db.get(Topic, entry.topic_id)
        if topic:
            topic.completed = 0
            topic.completion_pct = min(float(topic.completion_pct or 0.0), 99.0)

    await db.flush()
    return {"status": "unread", "entry_id": entry_id}


@router.post("/skip/{entry_id}", response_model=ScheduleRescheduleOut)
async def skip_and_reschedule(entry_id: str, db: AsyncSession = Depends(get_db)):
    """Skip a schedule entry and rebuild the upcoming plan starting from the next day."""
    entry = await db.get(ScheduleEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Schedule entry not found")

    if entry.completed:
        raise HTTPException(400, "Completed session cannot be skipped")

    user = await db.get(User, entry.user_id)
    daily_capacity_mins = max(int((user.daily_study_hours if user and user.daily_study_hours else 4) * 60), 60)
    next_day = entry.scheduled_date + timedelta(days=1)

    future_q = (
        select(ScheduleEntry)
        .where(
            ScheduleEntry.user_id == entry.user_id,
            ScheduleEntry.completed == 0,
            ScheduleEntry.id != entry.id,
            ScheduleEntry.scheduled_date >= next_day,
        )
        .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
    )
    future_result = await db.execute(future_q)
    future_entries = list(future_result.scalars().all())

    def _entry_duration_mins(item: ScheduleEntry) -> int:
        base_start = datetime.combine(item.scheduled_date, item.start_time)
        base_end = datetime.combine(item.scheduled_date, item.end_time)
        mins = int((base_end - base_start).total_seconds() // 60)
        return mins if mins > 0 else int(item.duration_mins or 60)

    same_day_starts = [item.start_time for item in future_entries if item.scheduled_date == next_day]
    day_start = min([entry.start_time, *same_day_starts], default=entry.start_time)

    gap_candidates: list[int] = []
    same_day_entries = [item for item in future_entries if item.scheduled_date == next_day]
    for current, nxt in zip(same_day_entries, same_day_entries[1:]):
        gap = int(
            (
                datetime.combine(next_day, nxt.start_time)
                - datetime.combine(next_day, current.end_time)
            ).total_seconds()
            // 60
        )
        if gap > 0:
            gap_candidates.append(gap)
    break_mins = min(gap_candidates) if gap_candidates else 15

    queue = [
        {
            "topic_id": entry.topic_id,
            "subject_name": entry.subject_name,
            "topic_name": entry.topic_name,
            "duration_mins": _entry_duration_mins(entry),
            "priority_score": min((entry.priority_score or 0) + 0.1, 1.5),
            "is_revision": 1,
            "is_skipped": True,
        }
    ]
    queue.extend(
        {
            "topic_id": item.topic_id,
            "subject_name": item.subject_name,
            "topic_name": item.topic_name,
            "duration_mins": _entry_duration_mins(item),
            "priority_score": item.priority_score,
            "is_revision": item.is_revision,
            "is_skipped": False,
        }
        for item in future_entries
    )

    for item in future_entries:
        await db.delete(item)
    await db.delete(entry)
    await db.flush()

    scheduled_entries: list[ScheduleEntry] = []
    current_day = next_day
    queue_idx = 0

    while queue_idx < len(queue):
        cursor = datetime.combine(current_day, day_start)
        planned_mins = 0

        while queue_idx < len(queue):
            item = queue[queue_idx]
            duration_mins = max(int(item["duration_mins"] or 60), 15)
            if planned_mins and planned_mins + duration_mins > daily_capacity_mins:
                break
            if not planned_mins and duration_mins > daily_capacity_mins:
                duration_mins = daily_capacity_mins

            start_dt = cursor
            end_dt = start_dt + timedelta(minutes=duration_mins)
            scheduled_entry = ScheduleEntry(
                user_id=entry.user_id,
                topic_id=item["topic_id"],
                subject_name=item["subject_name"],
                topic_name=item["topic_name"],
                scheduled_date=current_day,
                start_time=start_dt.time(),
                end_time=end_dt.time(),
                duration_mins=duration_mins,
                priority_score=float(item["priority_score"] or 0),
                is_revision=int(item["is_revision"] or 0),
                completed=0,
            )
            db.add(scheduled_entry)
            scheduled_entries.append(scheduled_entry)

            cursor = end_dt + timedelta(minutes=break_mins)
            planned_mins += duration_mins
            queue_idx += 1

        current_day += timedelta(days=1)

    await db.flush()
    new_entry = next(
        item for item in scheduled_entries
        if item.topic_id == entry.topic_id and item.subject_name == entry.subject_name and item.topic_name == entry.topic_name
    )
    await db.refresh(new_entry)

    return {
        "status": "skipped_rescheduled",
        "skipped_entry_id": entry_id,
        "rescheduled_entry": new_entry,
    }
