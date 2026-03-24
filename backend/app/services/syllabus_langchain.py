"""LangChain-powered syllabus chunking and Unit topic extraction."""

from __future__ import annotations

import re

from backend.app.services.syllabus_parser import extract_text_from_pdf, parse_subjects_and_topics
from backend.app.services.topic_text import humanize_topic_text, split_period_topic_list

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # pragma: no cover - optional at runtime
    RecursiveCharacterTextSplitter = None


_UNIT_HEADER_PATTERN = re.compile(
    r"^\s*unit\s*[-:]?\s*([ivxlcdm]+|\d+)\s*[:.\-]?\s*(.*)$",
    re.IGNORECASE,
)
_BULLET_PREFIX = re.compile(r"^\s*[\-\*\u2022\d\.\)\(]+\s*")
_TOPIC_SEPARATORS = re.compile(r"\s*(?:;|\||\u2022)\s*")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9\s:\-(),&%]")
_SINGLE_CHAR_RUN = re.compile(r"(.)\1{4,}")
_CURRICULUM_NOISE = (
    "course outcomes",
    "text books",
    "reference books",
    "scheme of examination",
    "credits",
    "ltp",
)
_OUTCOME_CODE_PATTERN = re.compile(r"\b(?:co|po|pso)\s*\d+\b", re.IGNORECASE)


def _roman_to_int(value: str) -> int | None:
    roman = value.strip().upper()
    if roman.isdigit():
        return int(roman)
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for char in reversed(roman):
        score = table.get(char)
        if score is None:
            return None
        if score < prev:
            total -= score
        else:
            total += score
            prev = score
    return total if total > 0 else None


def _is_topic_line(line: str) -> bool:
    candidate = line.strip()
    if len(candidate) < 3 or len(candidate) > 420:
        return False
    if not re.search(r"[A-Za-z]", candidate):
        return False
    if candidate.lower() in {"syllabus", "course outcomes", "text books"}:
        return False
    lowered = candidate.lower()
    if any(token in lowered for token in _CURRICULUM_NOISE):
        return False
    if _OUTCOME_CODE_PATTERN.search(candidate):
        return False
    if _SINGLE_CHAR_RUN.search(candidate):
        return False
    return True


def _normalize_topic_text(text: str) -> str:
    cleaned = humanize_topic_text(_NON_ALNUM.sub(" ", text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    return cleaned


def _is_quality_topic(text: str) -> bool:
    if not _is_topic_line(text):
        return False
    words = text.split()
    if len(words) < 2:
        if len(text) >= 5 and re.fullmatch(r"[A-Za-z][A-Za-z0-9\-()]+", text):
            return True
        return False
    return True


def _split_compound_topics(line: str) -> list[str]:
    candidate = re.sub(r"\s+", " ", line.strip())
    if not candidate:
        return []
    if ":" in candidate:
        # Keep the content after heading labels like "CO1: ...".
        _, suffix = candidate.split(":", 1)
        candidate = suffix.strip() or candidate

    # Keep commas/"and" inside a topic to preserve complete meaning.
    expanded_candidates = split_period_topic_list(candidate)
    parts: list[str] = []
    for item in expanded_candidates:
        parts.extend(part.strip(" .-") for part in _TOPIC_SEPARATORS.split(item))
    cleaned = [_normalize_topic_text(part) for part in parts]
    cleaned = [part for part in cleaned if _is_quality_topic(part)]
    return cleaned or ([candidate] if _is_topic_line(candidate) else [])


def _should_merge(prev: str, cur: str) -> bool:
    prev_s = prev.strip()
    cur_s = cur.strip()
    if not prev_s or not cur_s:
        return False
    if prev_s.endswith(("-", ",", ":", "(", "/")):
        return True
    if cur_s and cur_s[0].islower():
        return True
    if re.match(r"^(and|or|with|using|for|of|to|in|on|by)\b", cur_s, re.IGNORECASE):
        return True
    if len(cur_s.split()) <= 3 and not re.match(r"^\s*unit\s+\d+", cur_s, re.IGNORECASE):
        return True
    return False


def _merge_topic_fragments(topics: list[str]) -> list[str]:
    merged: list[str] = []
    for item in topics:
        cur = item.strip()
        if not cur:
            continue
        if merged and _should_merge(merged[-1], cur):
            merged[-1] = _normalize_topic_text(f"{merged[-1]} {cur}")
        else:
            merged.append(cur)
    return merged


def _cleanup_units(
    units: dict[int, list[str]],
    *,
    max_topics_per_unit: int = 40,
) -> dict[int, list[str]]:
    cleaned: dict[int, list[str]] = {}
    for unit, topics in units.items():
        seen: set[str] = set()
        curated: list[str] = []
        stitched = _merge_topic_fragments(topics)
        for raw in stitched:
            topic = _normalize_topic_text(raw)
            key = topic.lower()
            if not _is_topic_line(topic):
                continue
            if key in seen:
                continue
            seen.add(key)
            curated.append(topic)
            if len(curated) >= max_topics_per_unit:
                break
        if curated:
            cleaned[unit] = curated
    return cleaned


def _merge_units(
    *sources: dict[int, list[str]],
    unit_start: int,
    unit_end: int,
) -> dict[int, list[str]]:
    merged: dict[int, list[str]] = {unit: [] for unit in range(unit_start, unit_end + 1)}
    seen: set[tuple[int, str]] = set()
    for source in sources:
        for unit, topics in source.items():
            if unit < unit_start or unit > unit_end:
                continue
            for topic in topics:
                normalized = _normalize_topic_text(topic)
                key = (unit, normalized.lower())
                if not normalized or key in seen:
                    continue
                seen.add(key)
                merged[unit].append(normalized)
    return {unit: topics for unit, topics in merged.items() if topics}


def _extract_unit_topics_linewise(
    text: str,
    *,
    unit_start: int,
    unit_end: int,
) -> dict[int, list[str]]:
    units: dict[int, list[str]] = {unit: [] for unit in range(unit_start, unit_end + 1)}
    current_unit: int | None = None

    lines = [line.strip() for line in text.replace("\r", "\n").splitlines()]
    for raw in lines:
        line = _BULLET_PREFIX.sub("", raw).strip()
        if not line:
            continue

        unit_match = _UNIT_HEADER_PATTERN.match(line)
        if unit_match:
            unit_number = _roman_to_int(unit_match.group(1))
            current_unit = (
                unit_number
                if unit_number is not None and unit_start <= unit_number <= unit_end
                else None
            )
            suffix = unit_match.group(2).strip()
            if current_unit is not None and _is_topic_line(suffix):
                units[current_unit].extend(_split_compound_topics(suffix))
            continue

        if current_unit is None or not _is_topic_line(line):
            continue
        units[current_unit].extend(_split_compound_topics(line))

    return {unit: topics for unit, topics in units.items() if topics}


def _extract_units_from_parser_topics(
    text: str,
    *,
    unit_start: int,
    unit_end: int,
) -> dict[int, list[str]]:
    parsed_subjects = parse_subjects_and_topics(text)
    units: dict[int, list[str]] = {}
    for subject in parsed_subjects:
        for topic in subject.get("topics", []):
            line = str(topic).strip()
            match = _UNIT_HEADER_PATTERN.match(line)
            if not match:
                continue
            unit_number = _roman_to_int(match.group(1))
            if unit_number is None or unit_number < unit_start or unit_number > unit_end:
                continue
            suffix = match.group(2).strip()
            if not suffix:
                continue
            units.setdefault(unit_number, []).extend(_split_compound_topics(suffix))
    return units


def _split_with_langchain(
    text: str,
    *,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
) -> list[str]:
    if RecursiveCharacterTextSplitter is None:
        raise RuntimeError(
            "LangChain text splitters are not installed. Install 'langchain-text-splitters'."
        )
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ".", ";", " "],
    )
    return [chunk for chunk in splitter.split_text(text) if chunk.strip()]


def extract_unit_topics_with_langchain(
    text: str,
    *,
    unit_start: int = 1,
    unit_end: int = 5,
) -> dict[int, list[str]]:
    """Extract topic lines grouped by unit after LangChain chunking."""
    if unit_start > unit_end:
        raise ValueError("unit_start must be <= unit_end")

    chunks = _split_with_langchain(text)
    units: dict[int, list[str]] = {unit: [] for unit in range(unit_start, unit_end + 1)}
    seen: set[tuple[int, str]] = set()
    current_unit: int | None = None

    for chunk in chunks:
        for raw_line in chunk.splitlines():
            line = _BULLET_PREFIX.sub("", raw_line).strip()
            if not line:
                continue

            unit_match = _UNIT_HEADER_PATTERN.match(line)
            if unit_match:
                unit_number = _roman_to_int(unit_match.group(1))
                current_unit = (
                    unit_number
                    if unit_number is not None and unit_start <= unit_number <= unit_end
                    else None
                )
                suffix = unit_match.group(2).strip()
                if current_unit is not None and _is_topic_line(suffix):
                    for topic_part in _split_compound_topics(suffix):
                        key = (current_unit, topic_part.lower())
                        if key not in seen:
                            seen.add(key)
                            units[current_unit].append(topic_part)
                continue

            if current_unit is None or not _is_topic_line(line):
                continue

            for topic_part in _split_compound_topics(line):
                key = (current_unit, topic_part.lower())
                if key in seen:
                    continue
                seen.add(key)
                units[current_unit].append(topic_part)

    return {unit: topics for unit, topics in units.items() if topics}


def extract_unit_topics_from_pdf_with_langchain(
    pdf_bytes: bytes,
    *,
    unit_start: int = 1,
    unit_end: int = 5,
    max_topics_per_unit: int = 40,
    use_ocr_fallback: bool = True,
) -> dict[int, list[str]]:
    """Extract Unit topics from PDF bytes using hybrid parsing and cleanup."""
    text = extract_text_from_pdf(
        pdf_bytes,
        use_ocr_fallback=False,
    )
    units: dict[int, list[str]] = {}
    if text.strip():
        line_units = _extract_unit_topics_linewise(
            text,
            unit_start=unit_start,
            unit_end=unit_end,
        )
        parser_units = _extract_units_from_parser_topics(
            text,
            unit_start=unit_start,
            unit_end=unit_end,
        )
        lc_units: dict[int, list[str]] = {}
        try:
            lc_units = extract_unit_topics_with_langchain(
                text,
                unit_start=unit_start,
                unit_end=unit_end,
            )
        except RuntimeError:
            lc_units = {}
        units = _merge_units(
            line_units,
            parser_units,
            lc_units,
            unit_start=unit_start,
            unit_end=unit_end,
        )
        units = _cleanup_units(units, max_topics_per_unit=max_topics_per_unit)
        if units:
            return units

    if not use_ocr_fallback:
        return {}

    text = extract_text_from_pdf(
        pdf_bytes,
        use_ocr_fallback=True,
        ocr_max_pages=12,
    )
    if not text.strip():
        return {}

    line_units = _extract_unit_topics_linewise(
        text,
        unit_start=unit_start,
        unit_end=unit_end,
    )
    parser_units = _extract_units_from_parser_topics(
        text,
        unit_start=unit_start,
        unit_end=unit_end,
    )
    lc_units: dict[int, list[str]] = {}
    try:
        lc_units = extract_unit_topics_with_langchain(
            text,
            unit_start=unit_start,
            unit_end=unit_end,
        )
    except RuntimeError:
        # Optional dependency missing or another guarded runtime check; treat as a soft failure.
        lc_units = {}
    units = _merge_units(
        line_units,
        parser_units,
        lc_units,
        unit_start=unit_start,
        unit_end=unit_end,
    )
    return _cleanup_units(units, max_topics_per_unit=max_topics_per_unit)
