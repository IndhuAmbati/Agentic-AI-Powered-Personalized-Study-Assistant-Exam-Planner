"""Utilities for extracting subjects/topics from uploaded syllabus PDFs."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from pypdf import PdfReader

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional dependency
    fitz = None

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover - optional dependency
    RapidOCR = None


SUBJECT_PATTERNS = [
    re.compile(r"^\s*subject\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\s*paper\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\s*course\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\s*course\s*name\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\s*subject\s*name\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^\s*[A-Z]{2,}\s*-\s*(.+)$"),
]
COURSE_CODE_PATTERN = re.compile(r"^[A-Z]{1,4}\d{3}[A-Z]{1,4}$")
UNIT_PATTERN = re.compile(r"^\s*Unit\s*[-–]?\s*([IVXLC\d]+)\s*[:\-]?\s*(.*)$", re.IGNORECASE)
INSTITUTION_NOISE = {
    "bvrithcew",
    "b.tech.iiiyearii sem",
    "coursecode",
    "course code",
    "course title",
    "ltpcredits",
    "credits",
    "pre-requisite",
    "text books:",
    "course outcomes",
}
COURSE_SECTION_NOISE_PREFIXES = (
    "course description",
    "course outcomes",
    "text book",
    "text books",
    "reference book",
    "reference books",
)


def extract_text_from_pdf(
    pdf_bytes: bytes,
    *,
    use_ocr_fallback: bool = True,
    ocr_max_pages: int = 40,
) -> str:
    """
    Extract text from PDF.

    Strategy:
    1. pypdf text extraction
    2. PyMuPDF text extraction (fallback for encoded/complex PDFs)
    3. OCR via RapidOCR + PyMuPDF rendering (for scanned PDFs, if installed)
    """
    text_chunks: list[str] = []

    # 1) pypdf extraction
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_chunks.append(page_text)
    except Exception:
        pass

    combined = "\n".join(text_chunks).strip()
    if len(combined) >= 80:
        return combined

    # 2) PyMuPDF text extraction fallback
    if fitz is not None:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            extra_chunks: list[str] = []
            for page in doc:
                page_text = page.get_text("text") or ""
                if page_text.strip():
                    extra_chunks.append(page_text)
            doc.close()
            if extra_chunks:
                text_chunks.extend(extra_chunks)
                combined = "\n".join(text_chunks).strip()
                if len(combined) >= 80:
                    return combined
        except Exception:
            pass

    # 3) OCR fallback for scanned image PDFs
    if use_ocr_fallback and fitz is not None and RapidOCR is not None:
        try:
            ocr = RapidOCR()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            ocr_chunks: list[str] = []
            for page_idx, page in enumerate(doc):
                if page_idx >= ocr_max_pages:
                    break
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_bytes = pix.tobytes("png")
                result, _ = ocr(img_bytes)
                if not result:
                    continue
                page_lines: list[str] = []
                for item in result:
                    if not item or len(item) < 2:
                        continue
                    text = str(item[1]).strip()
                    if text:
                        page_lines.append(text)
                if page_lines:
                    ocr_chunks.append("\n".join(page_lines))
            doc.close()
            if ocr_chunks:
                text_chunks.extend(ocr_chunks)
        except Exception:
            pass

    return "\n".join(text_chunks).strip()


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_line(line: str) -> str:
    line = line.strip().strip("|")
    line = re.sub(r"\s+", " ", line)
    return line


def _is_probable_noise(line: str) -> bool:
    lower = line.lower()
    if re.fullmatch(r"\d{1,4}", line):
        return True
    if "page " in lower and re.search(r"\b\d+\b", lower):
        return True
    if lower in {"syllabus", "scheme of examination", "question paper pattern"}:
        return True
    return False


def _looks_like_topic(line: str) -> bool:
    if len(line) < 3 or len(line) > 220:
        return False
    if not re.search(r"[a-zA-Z]", line):
        return False
    if _is_probable_noise(line):
        return False
    if _looks_like_subject_heading(line):
        return False
    return True


def _clean_course_code(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())


def _looks_like_course_code(line: str) -> bool:
    return bool(COURSE_CODE_PATTERN.match(_clean_course_code(line)))


def _roman_to_int(token: str) -> int | None:
    value = token.strip().upper()
    if value == "VL":
        value = "V"
    elif value == "V1":
        value = "VI"
    elif re.fullmatch(r"I[L]+", value):
        value = value.replace("L", "I")
    elif value.endswith("C") and re.fullmatch(r"[IVXL]+C", value):
        value = value[:-1]
    if not value:
        return None
    if value.isdigit():
        return int(value)
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(value):
        score = table.get(ch)
        if score is None:
            return None
        if score < prev:
            total -= score
        else:
            total += score
            prev = score
    return total if total > 0 else None


def _extract_course_blocks(lines: list[str]) -> list[dict[str, Any]]:
    """
    Parse curriculum PDFs where each course starts with a code (e.g., SM601MS).
    """
    courses: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _looks_like_course_code(line):
            i += 1
            continue

        code = _clean_course_code(line)
        title = ""
        if i > 0:
            prev_line = _normalize_line(lines[i - 1])
            prev_prev_line = _normalize_line(lines[i - 2]) if i > 1 else ""
            if (
                prev_line
                and not UNIT_PATTERN.match(prev_prev_line)
                and not _looks_like_course_code(prev_line)
                and not UNIT_PATTERN.match(prev_line)
                and prev_line.lower() not in INSTITUTION_NOISE
                and len(prev_line) <= 120
                and not prev_line.lower().startswith(("professional elective", "open elective", "elective"))
            ):
                title = prev_line
        if not title and i + 1 < len(lines):
            next_line = _normalize_line(lines[i + 1])
            if (
                next_line
                and not _looks_like_course_code(next_line)
                and not UNIT_PATTERN.match(next_line)
                and next_line.lower() not in INSTITUTION_NOISE
                and len(next_line) <= 120
            ):
                title = next_line

        subject_name = f"{code} - {title}" if title else code
        unit_topics: list[str] = []
        extra_topics: list[str] = []
        last_unit_number: int | None = None
        current_unit_label: str | None = None

        def _append_topic(candidate: str) -> None:
            cleaned_candidate = _normalize_line(candidate)
            if not cleaned_candidate:
                return
            lowered_candidate = cleaned_candidate.lower()
            if lowered_candidate in INSTITUTION_NOISE:
                return
            if lowered_candidate.startswith(COURSE_SECTION_NOISE_PREFIXES):
                return
            if re.match(r"^c\d{3}\.\d", lowered_candidate):
                return
            if re.search(r"\b(?:isbn|edition|publishers?)\b", lowered_candidate):
                return
            if current_unit_label and not cleaned_candidate.lower().startswith(current_unit_label.lower()):
                cleaned_candidate = f"{current_unit_label}: {cleaned_candidate}"
            unit_topics.append(cleaned_candidate)

        j = i + 1
        while j < len(lines) and not _looks_like_course_code(lines[j]):
            item = _normalize_line(lines[j])
            if not item:
                j += 1
                continue

            unit_match = UNIT_PATTERN.match(item)
            if unit_match:
                unit_number = _roman_to_int(unit_match.group(1))
                if (
                    unit_number is not None
                    and last_unit_number is not None
                    and unit_number < last_unit_number
                ):
                    break
                suffix = unit_match.group(2).strip()
                if (
                    not suffix
                    and j + 1 < len(lines)
                    and not _looks_like_course_code(lines[j + 1])
                    and not UNIT_PATTERN.match(lines[j + 1])
                ):
                    suffix = _normalize_line(lines[j + 1])
                    j += 1
                current_unit_label = f"Unit {unit_match.group(1)}"
                topic = current_unit_label
                if suffix:
                    topic = f"{topic}: {suffix}"
                _append_topic(topic)
                if unit_number is not None:
                    last_unit_number = unit_number
                j += 1
                continue

            lowered = item.lower()
            if lowered in INSTITUTION_NOISE:
                j += 1
                continue

            if re.match(r"^c\d{3}\.\d", lowered):
                j += 1
                continue

            if lowered.startswith("course description") or lowered.startswith("course outcomes"):
                j += 1
                continue

            if current_unit_label is not None:
                _append_topic(item)
                j += 1
                continue

            if (
                len(item) <= 70
                and re.search(r"[a-zA-Z]", item)
                and not item.endswith(".")
                and not item.endswith(")")
                and ":" not in item
                and len(item.split()) <= 9
            ):
                # Short lines in course section are often topic titles.
                extra_topics.append(item)

            j += 1

        deduped: list[str] = []
        seen: set[str] = set()

        source_topics = unit_topics if unit_topics else extra_topics
        for topic in source_topics:
            key = topic.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(topic)

        if not deduped:
            deduped = ["Syllabus Overview"]

        courses.append({"name": subject_name, "topics": deduped})
        i = j

    return courses


def _looks_like_subject_heading(line: str) -> str | None:
    stripped = _normalize_line(line)
    if not stripped:
        return None

    for pattern in SUBJECT_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return match.group(1).strip()

    if stripped.isupper() and 3 <= len(stripped) <= 120 and "UNIT" not in stripped:
        return stripped.title()

    if re.match(r"^\s*(subject|course|paper)\s+\d+\b", stripped, re.IGNORECASE):
        return stripped

    return None


def parse_subjects_and_topics(
    text: str,
    *,
    expected_subject_count: int | None = None,
) -> list[dict[str, Any]]:
    """
    Heuristically parse subject/topic structure from raw syllabus text.

    Fallback behaviour:
    - If no subject headings are detected, returns one subject named "General Studies".
    - Topic candidates are picked from bullet lines or short non-empty lines.
    """
    normalized = _normalize_whitespace(text)
    if not normalized:
        return []

    # split both by lines and by separators; many PDFs merge bullets into long lines
    raw_lines: list[str] = []
    for line in normalized.splitlines():
        line = _normalize_line(line)
        if not line:
            continue
        raw_lines.append(line)
        if len(line) > 140:
            parts = [part.strip() for part in re.split(r"[;•]", line) if part.strip()]
            raw_lines.extend(parts)

    lines = []
    for line in raw_lines:
        cleaned = re.sub(r"^[\-\*\u2022\d\.\)\(]+\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)

    # First pass: explicit course-code based parsing for curriculum PDFs.
    course_blocks = _extract_course_blocks(lines)
    if course_blocks:
        if expected_subject_count and expected_subject_count > 0:
            return course_blocks[:expected_subject_count]
        return course_blocks

    subjects: list[dict[str, Any]] = []
    current_subject: dict[str, Any] | None = None

    for raw_line in lines:
        subject_name = _looks_like_subject_heading(raw_line)
        if subject_name:
            current_subject = {"name": subject_name, "topics": []}
            subjects.append(current_subject)
            continue

        candidate = _normalize_line(raw_line)
        if not _looks_like_topic(candidate):
            continue

        if current_subject is None:
            current_subject = {"name": "General Studies", "topics": []}
            subjects.append(current_subject)

        current_subject["topics"].append(candidate)

    cleaned: list[dict[str, Any]] = []
    for sub in subjects:
        seen: set[str] = set()
        deduped_topics: list[str] = []
        for topic in sub["topics"]:
            normalized = topic.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped_topics.append(topic)

        if deduped_topics:
            cleaned.append({"name": sub["name"], "topics": deduped_topics})

    # If no explicit subjects were detected, build generic subjects and spread topics.
    if not cleaned:
        all_candidates = [line for line in lines if _looks_like_topic(line)]
        deduped = []
        seen = set()
        for topic in all_candidates:
            key = topic.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(topic)

        if not deduped:
            return []

        if expected_subject_count and expected_subject_count > 1:
            groups: list[list[str]] = [[] for _ in range(expected_subject_count)]
            for idx, topic in enumerate(deduped):
                groups[idx % expected_subject_count].append(topic)
            cleaned = [
                {"name": f"Subject {idx + 1}", "topics": group or ["Syllabus Overview"]}
                for idx, group in enumerate(groups)
            ]
        else:
            cleaned = [{"name": "General Studies", "topics": deduped}]

    if expected_subject_count and expected_subject_count > 0:
        if len(cleaned) > expected_subject_count:
            cleaned = cleaned[:expected_subject_count]

    return cleaned
