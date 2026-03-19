"""Robust syllabus text extraction and subject/topic parsing."""

from __future__ import annotations

from io import BytesIO
import re

from pypdf import PdfReader
from backend.app.services.syllabus_parser import extract_text_from_pdf

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


COURSE_CODE_RE = re.compile(r"^[A-Z]{1,4}\d{3}[A-Z]{1,4}$")
COURSE_CODE_SEARCH_RE = re.compile(r"\b([A-Z]{1,4}\s*\d{3}\s*[A-Z]{1,4})\b")
UNIT_RE = re.compile(r"^\s*Unit\s*[-–]?\s*([IVXLC\d]+)\s*[:\-]?\s*(.*)$", re.IGNORECASE)
UNIT_ONLY_RE = re.compile(r"^\s*Unit\s*[-–]?\s*([IVXLC\d]+)\s*$", re.IGNORECASE)
NOISE_RE = re.compile(r"^(page\s+\d+|credits?|course outcomes?|text books?)$", re.IGNORECASE)
OUTCOME_CODE_RE = re.compile(
    r"\b(?:c|co|po|pso)\s*\d{2,4}(?:\s*[a-z]{1,3})?(?:\s*\.\s*\d+)?\b",
    re.IGNORECASE,
)
HEADER_NOISE_CONTAINS = (
    "bvrithcew",
    "b.tech",
    "course code",
    "course title",
    "ltpcredits",
    "ltp credits",
    "syllabus",
    "iii year",
    "ii sem",
    "course outcomes",
    "course outcome",
    "course description",
    "pre-requisite",
    "prerequisite",
    "reference books",
    "text books",
    "student's handbook",
)
TOPIC_NOISE_CONTAINS = (
    "course outcomes",
    "course outcome",
    "course description",
    "text books",
    "reference books",
    "pre-requisite",
    "prerequisite",
    "student's handbook",
    "publishers",
    "edition",
    "book house",
    "text book",
    "reference book",
    "after completion of this course",
    "after completion of thiscourse",
    "students will be able to",
    "studentswill be ableto",
    "objectives of the course",
    "the objectives of the course",
    "objectives of thecourse",
    "course are to understand",
    "courseare to understand",
    "gain expertise",
    "understanding of data handling",
    "understanding of datahandling",
    "software defined networking",
    "fundamentals of iot",
    "introduction to information retrieval",
    "construction of iot applications",
)
BOOK_CITATION_HINTS = (
    "mc graw",
    "mc-graw",
    "oxford press",
    "himalaya publishing",
    "oreilly",
    "morgan kaufmann",
    "publishers",
    "edition",
    "stanford univ",
    "tata mc",
    "publication",
    "publications",
    "press",
    "prentice hall",
    "john wiley",
    "wiley",
    "pearson",
    "cambridge",
    "pvt.ltd",
    "pvt ltd",
    "university press",
    "sons",
    "gerald",
    "paresh shah",
    "kumar",
    "zaki",
    "daniel",
)


def _is_course_code(line: str) -> bool:
    compact = re.sub(r"\s+", "", line.strip().upper())
    return bool(COURSE_CODE_RE.match(compact))


def _extract_course_code(line: str) -> str | None:
    if not line:
        return None
    match = COURSE_CODE_SEARCH_RE.search(line.upper())
    if not match:
        return None
    compact = re.sub(r"\s+", "", match.group(1))
    return compact if COURSE_CODE_RE.match(compact) else None


def _is_header_noise(line: str) -> bool:
    lower = line.lower().strip()
    if not lower:
        return True
    if _is_course_code(line):
        return False
    if NOISE_RE.search(lower):
        return True
    if any(token in lower for token in HEADER_NOISE_CONTAINS):
        return True
    if re.fullmatch(r"[A-Z0-9 &().-]{2,}", line) and len(line.split()) <= 6:
        return True
    return False


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _has_mostly_title_words(line: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z&()/.-]*", line)
    if len(words) < 2:
        return False
    titled = sum(1 for word in words if word[:1].isupper())
    return titled / len(words) >= 0.75


def _looks_like_course_title(line: str) -> bool:
    lower = line.lower().strip()
    if not lower or len(line) < 4 or len(line) > 140:
        return False
    if _is_header_noise(line):
        return False
    if _extract_course_code(line):
        return False
    if UNIT_RE.match(line) or UNIT_ONLY_RE.match(line):
        return False
    if OUTCOME_CODE_RE.search(line):
        return False
    if lower.startswith(("professional elective", "open elective", "elective")):
        return False
    if re.search(r"[:,;.]", line):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z&()/.-]*", line)
    if len(words) < 2 or len(words) > 10:
        return False
    return _has_mostly_title_words(line)


def _is_probable_subject_heading(line: str) -> bool:
    lower = line.lower().strip()
    if not lower or len(line) < 6 or len(line) > 140:
        return False
    if OUTCOME_CODE_RE.search(line):
        return False
    if _is_header_noise(line):
        return False
    if _extract_course_code(line):
        return False
    if UNIT_RE.match(line) or UNIT_ONLY_RE.match(line):
        return False
    if re.search(r"[:,;.]|\b(unit|chapter)\b", line, re.IGNORECASE):
        return False
    if line[:1].islower():
        return False
    if any(token in lower for token in TOPIC_NOISE_CONTAINS):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z&()/.-]*", line)
    if len(words) < 2 or len(words) > 8:
        return False

    subject_keywords = (
        "economics",
        "analysis",
        "analytics",
        "language",
        "processing",
        "accounting",
        "data",
        "business",
        "mining",
        "ai",
    )
    # Keep subject detection strict to avoid random topic lines becoming subjects.
    return any(keyword in lower for keyword in subject_keywords) and _has_mostly_title_words(line)


def _is_known_subject_name(name: str) -> bool:
    lower = name.lower().strip()
    phrases = (
        "business economics",
        "data analytics",
        "natural language processing",
        "information retrieval",
        "fundamentals of io t",
        "internet of things",
    )
    return any(p in lower for p in phrases)


def _is_subject_switch_suffix(suffix: str) -> bool:
    """Stricter check used only for 'Unit X: ...' lines."""
    text = suffix.strip()
    lower = text.lower()
    if not text:
        return False
    if re.search(r"[:,;.]|\b(unit|chapter)\b", text, re.IGNORECASE):
        return False
    if len(text.split()) > 7:
        return False
    if lower.startswith(
        (
            "introduction",
            "demand",
            "supply",
            "production",
            "cost",
            "pricing",
            "financial",
            "regression",
            "syntax",
            "structure",
            "object segmentation",
            "data management",
            "data visualization",
        )
    ):
        return False
    known_subject_phrases = (
        "business economics",
        "data analytics",
        "natural language processing",
        "information retrieval",
        "fundamentals of io t",
        "internet of things",
        "machine - to - machine",
        "machine to machine",
    )
    if any(phrase in lower for phrase in known_subject_phrases):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z&()/.-]*", text)
    if len(words) < 2:
        return False
    # Generic short title-case heading fallback
    titled = sum(1 for word in words if word[:1].isupper())
    return titled / len(words) >= 0.8


def _is_topic_candidate(line: str) -> bool:
    if len(line) < 4:
        return False
    if _is_header_noise(line):
        return False
    if _is_course_code(line):
        return False
    lower = line.lower().strip()
    if any(token in lower for token in TOPIC_NOISE_CONTAINS):
        return False
    if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z])?(?:\s+[A-Z][a-z]+)", line.strip()):
        return False
    if re.search(r"\b[a-z]\s*\d{2,3}\s*[a-z](?:\s*[a-z])?\b", line, re.IGNORECASE):
        return False
    if OUTCOME_CODE_RE.search(line):
        return False
    if re.fullmatch(r"unit\s*[-–]?\s*[ivxlcdm\d]+", lower):
        return False
    if re.fullmatch(r"[A-Za-z0-9 .,:;/()&-]{1,8}", line) and len(line.split()) <= 1:
        return False
    if re.search(r"\b(19|20)\d{2}\b", line):
        return False
    if re.search(r"\b\d+\s*(?:st|nd|rd|th)\s+edition\b", lower):
        return False
    if re.search(r"\b(?:isbn|vol\.?|volume)\b", lower):
        return False
    if "," in line and ":" not in line and line.count(".") >= 2:
        return False
    if re.search(r"\b(19|20)\d{2}\b", line) and any(h in lower for h in BOOK_CITATION_HINTS):
        return False
    if any(h in lower for h in BOOK_CITATION_HINTS):
        return False
    normalized = re.sub(r"[^a-z0-9]+", " ", lower).strip()
    if len(normalized.split()) >= 7 and re.search(
        r"\b(?:apply|analyze|build|develop|explore|evaluate|gain|demonstrate|understand|construct|carry out|comprehend|examine|achieve)\b",
        normalized,
        re.IGNORECASE,
    ):
        return False
    if line.count(",") >= 4:
        return False
    if not re.search(r"[A-Za-z]", line):
        return False
    return True


def _is_unit_topic(line: str) -> bool:
    return bool(UNIT_RE.match(line) or UNIT_ONLY_RE.match(line))


def _roman_to_int(value: str) -> int | None:
    token = value.strip().upper()
    if token.isdigit():
        return int(token)
    table = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    prev = 0
    for ch in reversed(token):
        cur = table.get(ch)
        if cur is None:
            return None
        total = total - cur if cur < prev else total + cur
        prev = max(prev, cur)
    return total if total > 0 else None


def _clean_text(line: str) -> str:
    cleaned = line.strip().strip("|")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"([A-Za-z])(\d)", r"\1 \2", cleaned)
    cleaned = re.sub(r"(\d)([A-Za-z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"\s*[-–]\s*", " - ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def extract_pdf_text_robust(pdf_bytes: bytes) -> str:
    """Extract text using pypdf first, then PyMuPDF fallback."""
    base_text = extract_text_from_pdf(
        pdf_bytes,
        use_ocr_fallback=True,
        ocr_max_pages=40,
    )
    if len(base_text.strip()) >= 80:
        return base_text

    chunks: list[str] = []
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                chunks.append(page_text)
    except Exception:
        pass

    text = "\n".join(chunks).strip()
    if len(text) >= 80:
        return text

    if fitz is not None:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            extra: list[str] = []
            for page in doc:
                page_text = page.get_text("text") or ""
                if page_text.strip():
                    extra.append(page_text)
            doc.close()
            if extra:
                return "\n".join(extra).strip()
        except Exception:
            pass

    return text or base_text


def parse_subjects_and_topics_robust(
    text: str,
    *,
    unit_start: int = 1,
    unit_end: int = 5,
    max_topics_per_subject: int = 200,
) -> dict[str, list[str]]:
    """Parse subject->topics with better handling for noisy OCR text."""
    lines: list[str] = []
    for raw in text.replace("\r", "\n").splitlines():
        candidate = _clean_text(raw)
        if not candidate:
            continue
        candidate = re.sub(r"^[\-\*\u2022\d\.\)\(]+\s*", "", candidate).strip()
        if not candidate:
            continue
        if _is_header_noise(candidate):
            continue
        lines.append(candidate)

    subjects: dict[str, list[str]] = {}
    current_subject = "General Studies"
    current_unit: int | None = None
    subjects.setdefault(current_subject, [])

    idx = 0
    while idx < len(lines):
        line = lines[idx]

        if current_subject == "General Studies" and _is_probable_subject_heading(line):
            normalized_heading = _clean_text(line)
            if normalized_heading:
                current_subject = normalized_heading
                subjects.setdefault(current_subject, [])
                current_unit = None
                idx += 1
                continue

        embedded_code = _extract_course_code(line)
        if embedded_code:
            title = ""
            prev_line = _clean_text(lines[idx - 1]) if idx > 0 else ""
            inline_title = _clean_text(COURSE_CODE_SEARCH_RE.sub("", line, count=1).strip(" -:"))
            if prev_line and _looks_like_course_title(prev_line):
                title = prev_line
            elif inline_title and _looks_like_course_title(inline_title):
                title = inline_title
            elif idx + 1 < len(lines) and not _is_course_code(lines[idx + 1]):
                nxt = lines[idx + 1]
                if _looks_like_course_title(nxt):
                    title = nxt
            code_display = embedded_code
            current_subject = f"{code_display} - {title}".strip(" -")
            subjects.setdefault(current_subject, [])
            idx += 1
            continue

        unit_match = UNIT_RE.match(line)
        if unit_match:
            unit_num = _roman_to_int(unit_match.group(1))
            suffix = _clean_text(unit_match.group(2))
            if unit_num and unit_start <= unit_num <= unit_end:
                current_unit = unit_num
                if suffix and _is_topic_candidate(suffix):
                    subjects[current_subject].append(f"Unit {unit_num}: {suffix}")
            else:
                current_unit = None
            idx += 1
            continue

        unit_only = UNIT_ONLY_RE.match(line)
        if unit_only:
            unit_num = _roman_to_int(unit_only.group(1))
            if unit_num and unit_start <= unit_num <= unit_end:
                next_line = _clean_text(lines[idx + 1]) if idx + 1 < len(lines) else ""
                if next_line and not UNIT_RE.match(next_line):
                    if _is_topic_candidate(next_line):
                        subjects[current_subject].append(f"Unit {unit_num}: {next_line}")
                    idx += 2
                    continue
            idx += 1
            continue

        if current_unit is not None and _is_topic_candidate(line):
            subjects[current_subject].append(f"Unit {current_unit}: {line}")
        # Ignore non-unit lines to prevent course description/book lines from polluting topics.

        idx += 1

    cleaned: dict[str, list[str]] = {}
    global_seen: set[str] = set()
    for subject_name, topics in subjects.items():
        seen: set[str] = set()
        deduped: list[str] = []
        for topic in topics:
            norm = _clean_text(topic)
            key = _normalize_key(norm)
            if not norm or key in seen:
                continue
            if key in global_seen:
                continue
            if _is_header_noise(norm):
                continue
            if OUTCOME_CODE_RE.search(norm):
                continue
            if any(token in norm.lower() for token in TOPIC_NOISE_CONTAINS):
                continue
            seen.add(key)
            global_seen.add(key)
            deduped.append(norm)
            if len(deduped) >= max_topics_per_subject:
                break
        is_general = subject_name.strip().lower() == "general studies"
        has_course_code = _extract_course_code(subject_name) is not None
        if deduped and not is_general and (
            has_course_code or len(deduped) >= 2 or _is_known_subject_name(subject_name)
        ):
            cleaned[subject_name] = deduped

    if not cleaned and subjects.get("General Studies"):
        fallback_topics = []
        seen: set[str] = set()
        for topic in subjects["General Studies"]:
            norm = _clean_text(topic)
            key = _normalize_key(norm)
            if not norm or key in seen or _is_header_noise(norm):
                continue
            if OUTCOME_CODE_RE.search(norm):
                continue
            if any(token in norm.lower() for token in TOPIC_NOISE_CONTAINS):
                continue
            if not _is_unit_topic(norm):
                continue
            seen.add(key)
            fallback_topics.append(norm)
            if len(fallback_topics) >= max_topics_per_subject:
                break
        if fallback_topics:
            cleaned["Imported Subject"] = fallback_topics

    return cleaned
