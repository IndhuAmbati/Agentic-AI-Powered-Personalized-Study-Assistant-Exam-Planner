"""Syllabus preview/confirm APIs for reliable schedule generation."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.database import get_db
from backend.app.models.progress import ProgressRecord
from backend.app.models.quiz import Quiz
from backend.app.models.schedule import ScheduleEntry
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.models.user import User
from backend.app.schemas.syllabus import (
    SyllabusConfirmOut,
    SyllabusConfirmRequest,
    SyllabusPreviewOut,
    SyllabusPreviewSubject,
)
from backend.app.services.robust_syllabus import (
    extract_pdf_text_robust,
    parse_subjects_and_topics_robust,
)
from backend.app.services.syllabus_parser import parse_subjects_and_topics
from backend.app.services.scheduler import (
    blocks_to_entries,
    generate_schedule,
    generate_schedule_rule_based,
)
from backend.app.services.topic_text import (
    humanize_topic_text,
    split_period_topic_list,
    topic_dedupe_key,
)

router = APIRouter(prefix="/api/syllabus", tags=["syllabus"])

_PREVIEW_SUBJECT_NOISE = {
    "general studies",
    "subject name",
    "topics (one per line)",
    "pre - requisite",
    "pre-requisite",
}

_UNIT_WITH_SUFFIX_PATTERN = re.compile(
    r"^\s*(Unit\s*[IVXLC\d]+)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_TOPIC_SEP_PATTERN = re.compile(r"\s*(?:;|\||\u2022)\s*")
_UNIT_ONLY_PATTERN = re.compile(r"^\s*Unit\s*-?\s*([IVXLC\d]+)\s*$", re.IGNORECASE)
_COURSE_CODE_TOKEN_PATTERN = re.compile(r"\b[A-Z]{1,4}\d{3}[A-Z]{0,4}\b", re.IGNORECASE)
_COURSE_CODE_PREFIX_PATTERN = re.compile(r"^\s*[A-Z]{1,4}\d{3}[A-Z]{0,4}\s*[-:]\s*", re.IGNORECASE)
_NOISE_TOPIC_EXACT = {"nit02"}
_NOISE_TOPIC_BODY_EXACT = {"a", "j", "np", "11 x", "802", "0/1"}
_NOISE_SINGLE_WORD_BODIES = {
    "information",
    "application",
    "applications",
    "database",
    "databases",
    "item",
    "items",
    "structure",
    "structures",
    "tech",
    "education",
    "title",
    "ltd",
    "conventional",
    "international",
    "julia",
    "julie",
    "meira",
    "tan",
    "tiwary",
    "kowalski",
    "maybury",
    "manning",
    "raghavan",
    "schutze",
    "maheshwari",
    "mithani",
    "solvency",
    "algorithms",
}
_ALLOWED_SINGLE_WORD_BODIES = {
    "introduction",
    "basics",
    "sensing",
    "actuation",
    "hypertext",
    "agriculture",
    "healthcare",
    "retrieval",
    "classification",
    "overfitting",
    "regression",
    "logistic",
    "production",
    "cost",
    "solvency",
}
_FRAGMENT_ENDINGS = {"and", "of", "to", "for", "with", "or", "amp", "etc", "application", "applications"}
_NOISE_TOPIC_PHRASES = {
    "pre requisite",
    "pre - requisite",
    "prerequisite",
    "syllabus",
    "course outcome",
    "course outcomes",
    "course description",
    "cse ai ml syllabus",
    "reference books",
    "text books",
    "text book",
    "reference book",
    "student s handbook",
    "course title",
    "lt pcredits",
    "ltpcredits",
    "open elective",
    "professional elective",
    "book house",
    "himalaya publishing",
    "stan ford",
    "univ",
    "after completion of this course",
    "after completion of thiscourse",
    "students will be able to",
    "studentswill be ableto",
    "objectives of the course",
    "the objectives of the course",
    "objectives of thecourse",
    "course are to understand",
    "courseare to understand",
    "be able to",
    "students will be ableto",
    "be ableto",
    "develop a clear comprehension",
    "gain expertise",
    "understanding of data handling",
    "understanding of datahandling",
    "software defined networking",
    "fundamentals of iot",
    "introduction to information retrieval",
    "construction of iot applications",
    "ii year ii semester",
    "il year il semester",
    "contains supervised and unsupervised models",
    "search methods and visualization techniques",
    "language processing applications",
    "inlationus",
    "markttucture",
    "monolyligopolyonplist",
}
_BOOKISH_HINTS = {
    "mc graw",
    "mcgraw",
    "wiley",
    "pearson",
    "cambridge",
    "oxford",
    "press",
    "publication",
    "publications",
    "publishers",
    "edition",
    "tata",
    "morgan kaufmann",
    "oreilly",
    "geethika",
    "ghosh",
    "piyali",
    "roy choudhury",
    "chaturvedi",
    "gupta",
    "frakes",
    "baeza",
    "leskovec",
    "ullman",
    "maheswaran",
    "maheshwaran",
    "rajaraman",
    "ilinsky",
    "dhanesh",
    "khatri",
    "maheshwari",
    "mithani",
    "siddiqui",
    "zitouni",
    "bikel",
    "manning",
    "raghavan",
    "schutze",
    "kowalski",
    "maybury",
    "prentice hall",
    "gerald",
    "mark t",
    "paresh shah",
    "kumar",
    "zaki",
    "daniel",
    "steinbach",
    "ste inbach",
}
_NOISE_TOPIC_PREFIXES = (
    "course title",
    "ii year",
    "iii year",
    "lt pcredits",
    "ltpcredits",
    "open elective",
    "professional elective",
)
_FRAGMENT_STARTERS = {"of", "and", "to", "for", "in", "with", "by", "on", "from"}
_SAFE_TITLE_WORDS = {
    "advanced",
    "analytics",
    "basics",
    "concepts",
    "concept",
    "design",
    "economics",
    "hypertext",
    "indexing",
    "introduction",
    "language",
    "linkages",
    "management",
    "modeling",
    "processing",
    "retrieval",
    "revision",
    "structures",
    "systems",
    "techniques",
    "themes",
    "topics",
    "visualization",
}
_BROKEN_COURSE_CODE_PATTERN = re.compile(r"\b[a-z]\s*\d{2,3}\s*[a-z](?:\s*[a-z])?\b", re.IGNORECASE)
_OUTCOME_VERB_PATTERN = re.compile(
    r"\b(?:apply|analyze|build|develop|explore|evaluate|gain|demonstrate|understand|construct|carry out|comprehend|examine|achieve)\b",
    re.IGNORECASE,
)
_TECHNICAL_TOPIC_HINTS = {
    "regression",
    "time series",
    "learning models",
    "objective segmentation",
    "language models",
    "performance",
    "probability",
    "statistics",
    "pricing",
    "economics",
}
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


def _roman_to_int(token: str) -> int | None:
    t = token.strip().upper()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
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


def _detect_unit_bounds(text: str) -> tuple[int, int]:
    matches = re.findall(r"\bUnit\s*[-–]?\s*([IVXLC\d]+)\b", text or "", flags=re.IGNORECASE)
    values = sorted({_roman_to_int(m) for m in matches if _roman_to_int(m) is not None})
    if not values:
        return (1, 5)
    return (values[0], values[-1])


def _is_valid_preview_subject(name: str, topics: list[str]) -> bool:
    title = _clean_subject_name(name).lower()
    if not title or title in _PREVIEW_SUBJECT_NOISE:
        return False
    if ":" in title or title.startswith("unit "):
        return False
    alpha_words = re.findall(r"[a-zA-Z][a-zA-Z&-]*", title)
    if len(alpha_words) < 2 or len(alpha_words) > 14:
        return False
    if len(topics) < 2:
        return False
    return True


def _normalize_subject_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _strip_course_code_prefix(name: str) -> str:
    return _COURSE_CODE_PREFIX_PATTERN.sub("", _clean_subject_name(name)).strip()


def _subject_alias_key(name: str) -> str:
    return _normalize_subject_key(_strip_course_code_prefix(name))


def _clean_subject_name(name: str) -> str:
    cleaned = _humanize_topic_text(name)
    cleaned = cleaned.translate(_UNICODE_ROMAN_MAP)
    cleaned = re.sub(r"\bUnit\s*-\s*([IVXLC]+)\b", r"Unit \1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bUnit\s+VL\b", "Unit V", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bUnit\s+V1\b", "Unit VI", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"^\s*ntroduction\b", "Introduction", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bElective\s*-\s*Il\b", "Elective-II", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"([a-z])and([A-Z])", r"\1 and \2", cleaned)
    cleaned = cleaned.replace("&", " & ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def _is_strong_subject_name(name: str) -> bool:
    text = _clean_subject_name(name)
    if not text:
        return False
    lower = text.lower()
    if lower.startswith("introduction to "):
        return False
    if re.match(r"^[A-Z]{1,4}\d{3}[A-Z]{1,4}\s*[-:]", text):
        return True
    if re.match(r"^[A-Z]\s*\d{3}\s*[A-Z]{0,2}$", text):
        return False
    if ":" in text or lower.startswith("unit "):
        return False
    if re.search(r"\d", text) and "-" not in text and len(re.findall(r"[A-Za-z][A-Za-z&-]*", text)) <= 3:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z&-]*", text)
    return 2 <= len(words) <= 14


def _is_valid_topic_text(topic_name: str, subject_name: str | None = None) -> bool:
    text = _humanize_topic_text(topic_name)
    if not text:
        return False
    lower = text.lower()
    if _UNIT_ONLY_PATTERN.match(text):
        return False
    if lower in _NOISE_TOPIC_EXACT:
        return False
    if _COURSE_CODE_TOKEN_PATTERN.search(text) or re.search(r"\bnit\s*\d+\b", lower):
        return False
    body_text = re.sub(r"^\s*Unit\s*[IVXLC\d]+\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    if re.match(r"^unit\s+[ivxlcdm\d]+\b$", body_text, re.IGNORECASE):
        return False
    title_words = re.findall(r"[A-Za-z]+", body_text)
    if (
        re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z])?(?:\s+[A-Z][a-z]+)", body_text)
        and not any(word.lower() in _SAFE_TITLE_WORDS for word in title_words)
    ):
        return False
    body = body_text.lower()
    if body in _NOISE_TOPIC_BODY_EXACT:
        return False
    normalized_body = re.sub(r"[^a-z0-9]+", " ", body).strip()
    if any(phrase in normalized_body for phrase in _NOISE_TOPIC_PHRASES):
        return False
    if re.search(r"(?:https?://|www\.|\.edu\b|@)", lower):
        return False
    if re.search(r"\bb\s*tech\b|\bsyllahus\b|\bsyllabus\b", normalized_body):
        return False
    if re.match(r"^bh\s*\d+", normalized_body):
        return False
    if re.match(r"^\d+\s+[a-z]", body):
        return False
    if _BROKEN_COURSE_CODE_PATTERN.search(text):
        return False
    if body.startswith(_NOISE_TOPIC_PREFIXES):
        return False
    if re.search(r"\btextbooks?\b|\breference books?\b", body):
        return False
    if any(hint in normalized_body for hint in _BOOKISH_HINTS):
        return False
    if re.search(r"\b(?:pvt|ltd|edition|isbn|publishers?|university|college|hyderabad)\b", normalized_body):
        return False
    if re.search(r"\b(?:19|20)\d{2}\b", normalized_body):
        return False
    if (
        len(body_words := [w for w in re.split(r"\s+", re.sub(r"[^A-Za-z0-9 ]", " ", body)) if w]) >= 7
        and _OUTCOME_VERB_PATTERN.search(normalized_body)
        and not any(hint in normalized_body for hint in _TECHNICAL_TOPIC_HINTS)
    ):
        return False
    if (
        3 <= len(body_words) <= 5
        and "and" in {word.lower() for word in body_words}
        and all(word.lower() == "and" or word[:1].isupper() for word in body_words)
        and not any(word.lower() in _SAFE_TITLE_WORDS for word in body_words)
    ):
        return False
    if re.fullmatch(r"[\d\W_]+", body):
        return False
    if len(re.findall(r"[A-Za-z]{3,}", body)) == 0:
        return False
    if subject_name and (
        _normalize_subject_key(text) == _normalize_subject_key(subject_name)
        or _normalize_subject_key(text) == _subject_alias_key(subject_name)
    ):
        return False
    if not body_words:
        return False
    if len(body_words) == 1:
        word = body_words[0].lower()
        if word in _NOISE_SINGLE_WORD_BODIES:
            return False
        if word not in _ALLOWED_SINGLE_WORD_BODIES:
            return False
    if len(body_words) < 2 and body_words[0].lower() in _NOISE_SINGLE_WORD_BODIES:
        return False
    if body_words[0].lower() in _FRAGMENT_STARTERS:
        return False
    if body_words[0].lower() in {"of", "and", "to", "for", "in", "with", "by", "on"} and len(body_words) < 4:
        return False
    if body_words[-1].lower() in _FRAGMENT_ENDINGS and len(body_words) <= 6:
        return False
    if len(body_words) >= 12 and re.search(
        r"\b(this|also|covers|apply|analyze|develop|examine|comprehend|explore|build|evaluate|carry out)\b",
        normalized_body,
    ):
        return False
    return True


def _merge_parsed_subjects(
    robust_parsed: dict[str, list[str]],
    classic_parsed: list[dict[str, object]],
    *,
    max_topics_per_subject: int,
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {
        _clean_subject_name(name): _normalize_topic_items(list(topics), _clean_subject_name(name))
        for name, topics in robust_parsed.items()
        if _clean_subject_name(name) and _is_strong_subject_name(name)
    }
    key_to_name = {_normalize_subject_key(name): name for name in merged.keys()}
    alias_to_name = {_subject_alias_key(name): name for name in merged.keys() if _subject_alias_key(name)}

    for subject in classic_parsed:
        raw_name = _clean_subject_name(subject.get("name") or "")
        if not raw_name:
            continue
        raw_topics = [str(topic or "").strip() for topic in list(subject.get("topics") or [])]
        expanded_topics = _normalize_topic_items(raw_topics, raw_name)
        if not expanded_topics:
            continue

        key = _normalize_subject_key(raw_name)
        alias_key = _subject_alias_key(raw_name)
        target_name = key_to_name.get(key) or alias_to_name.get(alias_key)
        if target_name is None:
            # If robust parser already found subjects, do not create new subjects from classic parser.
            if merged:
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

    return merged


def _should_prefer_classic_subjects(
    robust_subjects: dict[str, list[str]],
    classic_subjects: list[dict[str, object]],
) -> bool:
    if not classic_subjects:
        return False
    classic_with_codes = [
        item for item in classic_subjects if _COURSE_CODE_TOKEN_PATTERN.search(str(item.get("name") or ""))
    ]
    if not classic_with_codes:
        return False
    robust_count = sum(len(topics) for topics in robust_subjects.values())
    classic_count = sum(len(list(item.get("topics") or [])) for item in classic_subjects)
    if robust_count == 0:
        return True
    return robust_count >= max(classic_count * 2, classic_count + 20)


def _humanize_topic_text(text: str) -> str:
    cleaned = humanize_topic_text(text)
    cleaned = (
        cleaned.replace("．", ".")
        .replace("。", ".")
        .replace("｡", ".")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("•", ".")
        .replace("·", ".")
    )
    substitutions = (
        (r"\bBayesiantopicbased\b", "Bayesian topic based"),
        (r"\bofword senses\b", "of word senses"),
        (r"\bforprediction\b", "for prediction"),
        (r"\bConcep\s+tof\b", "Concept of"),
        (r"SDN\)\s*SDN", "SDN) SDN"),
    )
    for pattern, replacement in substitutions:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def _split_period_topic_list(text: str) -> list[str]:
    return split_period_topic_list(text)


def _topic_dedupe_key(text: str) -> str:
    return topic_dedupe_key(text)


def _normalize_topic_items(raw_topics: list[str], subject_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_topic in _expand_unit_only_topics([str(topic or "") for topic in raw_topics]):
        split_candidates = _split_topic_candidates(str(raw_topic))
        if not split_candidates:
            candidate = _humanize_topic_text(str(raw_topic)).strip()
            split_candidates = [candidate] if candidate else []
        for topic_name in split_candidates:
            if not _is_valid_topic_text(topic_name, subject_name):
                continue
            topic_key = _topic_dedupe_key(topic_name)
            if topic_key in seen:
                continue
            seen.add(topic_key)
            normalized.append(topic_name[:300])
    return normalized


def _sanitize_schedule_entries(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    for entry in entries:
        entry.subject_name = _humanize_topic_text(entry.subject_name or "")
        entry.topic_name = _humanize_topic_text(entry.topic_name or "")
    return entries


def _merge_confirm_subject_payload(
    subjects: list[SyllabusPreviewSubject],
) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for subject_item in subjects:
        subject_name = _clean_subject_name(subject_item.name)
        if not subject_name or not _is_strong_subject_name(subject_name):
            continue
        subject_key = _subject_alias_key(subject_name) or _normalize_subject_key(subject_name)
        if not subject_key:
            continue

        bucket = merged.get(subject_key)
        if bucket is None:
            bucket = {"name": subject_name, "topics": [], "seen": set()}
            merged[subject_key] = bucket

        seen_topics = bucket["seen"]
        for topic_name in _normalize_topic_items(list(subject_item.topics), str(bucket["name"])):
            topic_key = _topic_dedupe_key(topic_name)
            if topic_key in seen_topics:
                continue
            seen_topics.add(topic_key)
            bucket["topics"].append(topic_name[:300])

    return [
        {"name": str(item["name"]), "topics": list(item["topics"])}
        for item in merged.values()
        if item["topics"]
    ]


def _split_topic_candidates(raw_topic: str) -> list[str]:
    cleaned = _humanize_topic_text(raw_topic)
    if not cleaned:
        return []
    if _UNIT_ONLY_PATTERN.match(cleaned):
        return []
    cleaned = re.sub(r"\btopicbased\b", "topic based", cleaned, flags=re.IGNORECASE)

    unit_prefix = ""
    topic_body = cleaned
    unit_match = _UNIT_WITH_SUFFIX_PATTERN.match(cleaned)
    if unit_match:
        unit_prefix = unit_match.group(1).strip()
        topic_body = unit_match.group(2).strip()

    split_parts: list[str] = []
    for part in _TOPIC_SEP_PATTERN.split(topic_body):
        p = part.strip(" .,-")
        if not p:
            continue
        if ":" in p:
            head, tail = p.split(":", 1)
            normalized_head = re.sub(r"[^a-z0-9]+", " ", head.lower()).strip()
            if any(hint in normalized_head for hint in _BOOKISH_HINTS):
                continue
            if len(head.split()) <= 4 and len(tail.split()) >= 2:
                p = tail.strip()
        comma_parts = [s.strip(" .,-") for s in p.split(",") if s.strip(" .,-")]
        # Split by comma only when it looks like a topic list, not prose.
        first_part = comma_parts[0] if len(comma_parts) >= 1 else ""
        second_part = comma_parts[1] if len(comma_parts) >= 2 else ""
        short_comma_list = (
            2 <= len(comma_parts) <= 5
            and all(len(x.split()) <= 8 for x in comma_parts)
            and any(len(x.split()) >= 2 for x in comma_parts)
        )
        protected_two_part_phrase = (
            re.search(r"\bwords?\s+and\s+their\s+components\b", first_part, re.IGNORECASE)
            or (
                len(comma_parts) >= 2
                and re.search(r"\bprogramming\b", first_part, re.IGNORECASE)
                and re.search(r"\bintegration\b", second_part, re.IGNORECASE)
            )
            or (
                len(comma_parts) >= 2
                and re.search(r"\bissues?\b|\bchallenges?\b", second_part, re.IGNORECASE)
            )
        )
        should_split_two = (
            len(comma_parts) == 2
            and all(len(x.split()) <= 10 for x in comma_parts)
            and not comma_parts[1].lower().startswith(("and ", "or "))
            and not protected_two_part_phrase
        )
        if (
            len(comma_parts) == 2
            and len(comma_parts[1].split()) <= 5
            and _is_valid_topic_text(
                f"{unit_prefix}: {comma_parts[1]}" if unit_prefix else comma_parts[1]
            )
        ):
            should_split_two = True
        if (short_comma_list and len(comma_parts) >= 3) or should_split_two:
            for comma_part in comma_parts:
                split_parts.extend(_split_period_topic_list(comma_part))
        else:
            split_parts.extend(_split_period_topic_list(p))

    seen: set[str] = set()
    finalized: list[str] = []
    for part in split_parts:
        item = _humanize_topic_text(part)
        if not item:
            continue
        if _UNIT_ONLY_PATTERN.match(item):
            continue
        full = f"{unit_prefix}: {item}" if unit_prefix else item
        if re.match(r"^\s*Unit\s*[IVXLC\d]+\s*:\s*Unit\s*[IVXLC\d]+\s*$", full, re.IGNORECASE):
            continue
        if not _is_valid_topic_text(full):
            continue
        key = _topic_dedupe_key(full)
        if key in seen:
            continue
        seen.add(key)
        finalized.append(full[:300])
    return finalized


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


def _topic_unit_number(name: str) -> int | None:
    match = re.match(r"^\s*Unit\s*([IVXLC\d]+)\b", _humanize_topic_text(name), re.IGNORECASE)
    return _roman_to_int(match.group(1)) if match else None


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
        for topic in topics:
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


@router.post("/preview-pdf", response_model=SyllabusPreviewOut)
async def preview_pdf(
    file: UploadFile = File(...),
    unit_start: int | None = Form(None),
    unit_end: int | None = Form(None),
    max_topics_per_subject: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(400, "Uploaded PDF is empty")

    text = extract_pdf_text_robust(raw_bytes)
    detected_start, detected_end = _detect_unit_bounds(text)
    if unit_start is None and unit_end is None:
        # Keep simple mode robust: many PDFs under-detect the last unit (commonly Unit 5).
        start, end = 1, max(5, detected_end)
    else:
        start = unit_start if unit_start is not None else detected_start
        end = unit_end if unit_end is not None else max(detected_end, start)
    if start < 1 or end < start:
        raise HTTPException(400, "Invalid unit range")
    max_topics = max_topics_per_subject if max_topics_per_subject is not None else 200
    if max_topics < 5 or max_topics > 300:
        raise HTTPException(400, "max_topics_per_subject must be between 5 and 300")

    robust_parsed = parse_subjects_and_topics_robust(
        text,
        unit_start=start,
        unit_end=end,
        max_topics_per_subject=max_topics,
    )
    classic_parsed = parse_subjects_and_topics(text)
    if _should_prefer_classic_subjects(robust_parsed, classic_parsed):
        parsed = {
            _clean_subject_name(str(item.get("name") or "")): _normalize_topic_items(
                list(item.get("topics") or []),
                _clean_subject_name(str(item.get("name") or "")),
            )
            for item in classic_parsed
            if _clean_subject_name(str(item.get("name") or ""))
        }
    else:
        parsed = _merge_parsed_subjects(
            robust_parsed,
            classic_parsed,
            max_topics_per_subject=max_topics,
        )
    parsed = {
        name: topics
        for name, topics in parsed.items()
        if _is_valid_preview_subject(name, topics)
    }
    if not parsed:
        raise HTTPException(400, "Could not extract subjects/topics from this PDF")

    subjects = [SyllabusPreviewSubject(name=name, topics=topics) for name, topics in parsed.items()]
    return {
        "subjects_detected": len(subjects),
        "topics_detected": sum(len(subject.topics) for subject in subjects),
        "subjects": subjects,
    }


@router.delete("/reset/{user_id}")
async def reset_user_data(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    topic_ids_result = await db.execute(
        select(Topic.id)
        .join(Subject, Topic.subject_id == Subject.id)
        .where(Subject.user_id == user_id)
    )
    topic_ids = list(topic_ids_result.scalars().all())

    await db.execute(delete(ScheduleEntry).where(ScheduleEntry.user_id == user_id))
    await db.execute(delete(ProgressRecord).where(ProgressRecord.user_id == user_id))
    if topic_ids:
        await db.execute(delete(ProgressRecord).where(ProgressRecord.topic_id.in_(topic_ids)))
        await db.execute(delete(ScheduleEntry).where(ScheduleEntry.topic_id.in_(topic_ids)))
        await db.execute(delete(Quiz).where(Quiz.topic_id.in_(topic_ids)))
        await db.execute(delete(Topic).where(Topic.id.in_(topic_ids)))
    await db.execute(delete(Quiz).where(Quiz.user_id == user_id))
    await db.execute(delete(Subject).where(Subject.user_id == user_id))
    await db.flush()
    return {"status": "ok", "message": "User study data cleared"}


@router.post("/confirm-and-generate", response_model=SyllabusConfirmOut, status_code=201)
async def confirm_and_generate(
    payload: SyllabusConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, payload.user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if payload.start_date > payload.end_date:
        raise HTTPException(400, "start_date must be on or before end_date")
    if not payload.subjects:
        raise HTTPException(400, "subjects list cannot be empty")

    if payload.clear_existing:
        await db.execute(delete(ScheduleEntry).where(ScheduleEntry.user_id == payload.user_id))
        await db.execute(delete(ProgressRecord).where(ProgressRecord.user_id == payload.user_id))
        await db.execute(delete(Quiz).where(Quiz.user_id == payload.user_id))
        await db.execute(delete(Subject).where(Subject.user_id == payload.user_id))
        await db.flush()

    created_subject_ids: list[str] = []
    topics_created = 0
    topic_order = 0

    normalized_subjects = _merge_confirm_subject_payload(payload.subjects)
    for subject_item in normalized_subjects:
        subject_name = str(subject_item["name"])
        subject = Subject(
            user_id=payload.user_id,
            name=subject_name[:200],
            exam_date=None,
            priority=3.0,
            color="#4A90D9",
        )
        db.add(subject)
        await db.flush()
        created_subject_ids.append(subject.id)

        for topic_name in list(subject_item["topics"]):
            db.add(
                Topic(
                    subject_id=subject.id,
                    name=str(topic_name)[:300],
                    difficulty=payload.default_topic_difficulty,
                    estimated_hours=payload.default_topic_hours,
                    order_index=topic_order,
                )
            )
            topics_created += 1
            topic_order += 1

    if not created_subject_ids or topics_created == 0:
        raise HTTPException(400, "No valid subjects/topics to save")

    await db.flush()

    result = await db.execute(
        select(Subject)
        .where(Subject.id.in_(created_subject_ids))
        .options(selectinload(Subject.topics))
    )
    subjects = list(result.scalars().unique().all())

    await db.execute(
        delete(ScheduleEntry).where(
            ScheduleEntry.user_id == payload.user_id,
            ScheduleEntry.scheduled_date >= payload.start_date,
            ScheduleEntry.scheduled_date <= payload.end_date,
        )
    )

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
            distribute_across_range=False,
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
        )
    entries = blocks_to_entries(payload.user_id, blocks)
    db.add_all(entries)
    await db.flush()

    subject_name_by_id = {subject.id: subject.name for subject in subjects}
    topic_records = [topic for subject in subjects for topic in subject.topics]
    revision_entries = _build_revision_entries_between(
        user_id=payload.user_id,
        start_date=payload.start_date,
        end_date=payload.end_date,
        session_duration_mins=payload.session_duration_mins,
        topics=topic_records,
        subject_name_by_id=subject_name_by_id,
        study_entries=entries,
    )
    if revision_entries:
        db.add_all(revision_entries)
        await db.flush()

    entries_result = await db.execute(
        select(ScheduleEntry)
        .where(
            ScheduleEntry.user_id == payload.user_id,
            ScheduleEntry.scheduled_date >= payload.start_date,
            ScheduleEntry.scheduled_date <= payload.end_date,
        )
        .order_by(ScheduleEntry.scheduled_date, ScheduleEntry.start_time)
    )
    schedule_entries = list(entries_result.scalars().all())

    return {
        "subjects_created": len(created_subject_ids),
        "topics_created": topics_created,
        "schedule_entries": _sanitize_schedule_entries(schedule_entries),
    }
