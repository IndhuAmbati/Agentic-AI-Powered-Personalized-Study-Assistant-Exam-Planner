"""Shared text normalization helpers for OCR-derived topic strings."""

from __future__ import annotations

import re

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

def _normalize_unit_prefixes(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = _roman_to_int(match.group(1))
        if value is None:
            return match.group(0)
        return f"Unit {value}"
    return re.sub(r"\bUnit\s+([IVXLC\d]+)\b", repl, text, flags=re.IGNORECASE)


def _split_joined_function_word_suffixes(text: str) -> str:
    pattern = re.compile(
        rf"\b([A-Za-z]{{4,}}?)({'|'.join(_FUNCTION_WORDS)})(?=\b)",
        re.IGNORECASE,
    )
    previous = None
    cleaned = text
    while cleaned != previous:
        previous = cleaned
        cleaned = pattern.sub(r"\1 \2", cleaned)
    return cleaned

def _should_merge_ocr_tokens(left: str, right: str) -> bool:
    if not (left.isalpha() and right.isalpha()):
        return False
    if left[:1].isupper() and right[:1].isupper() and len(left) > 3 and len(right) > 3:
        return False
    if len(left) == 1 or len(right) == 1:
        return False
    if right.lower() in _DO_NOT_MERGE_RIGHT:
        return False

    merged = f"{left}{right}"
    if not 5 <= len(merged) <= 24:
        return False

    right_lower = right.lower()
    left_tail = left[-2:].lower()
    right_head = right[:2].lower()
    right_starts_lower = right[:1].islower()
    bridge_from_consonant_to_vowel = (
        all(ch not in "aeiou" for ch in left_tail if ch.isalpha())
        and any(ch in "aeiou" for ch in right_head if ch.isalpha())
    )

    return right_starts_lower and (
        len(left) <= 4
        or len(right) <= 4
        or bridge_from_consonant_to_vowel
        or right_lower in _OCR_SUFFIX_FRAGMENTS
    )


def _merge_fragmented_tokens(text: str) -> str:
    token_pattern = re.compile(r"\b([A-Za-z]{2,})\s+([A-Za-z]{2,12})\b")
    previous = None
    cleaned = text
    while previous != cleaned:
        previous = cleaned

        def repl(match: re.Match[str]) -> str:
            left = match.group(1)
            right = match.group(2)
            if _should_merge_ocr_tokens(left, right):
                return f"{left}{right}"
            return match.group(0)

        cleaned = token_pattern.sub(repl, cleaned)
    return cleaned

def looks_like_reference_text(text: str) -> bool:
    source = str(text or "").strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", source.lower()).strip()
    if not normalized:
        return False
    alpha_tokens = re.findall(r"[A-Za-z]+", source)
    if not alpha_tokens:
        return False
    if re.search(r"\b(19|20)\d{2}\b", source) and (source.count(",") >= 1 or source.count(".") >= 2):
        return True
    if len(alpha_tokens) >= 4 and sum(1 for token in alpha_tokens if len(token) == 1) >= 2:
        return True
    if source.count(",") >= 2:
        titled = sum(1 for token in alpha_tokens if token[:1].isupper())
        if titled / max(len(alpha_tokens), 1) >= 0.7 and ":" not in source:
            return True
    if source.count(".") >= 2 and source.count(",") >= 1:
        return True
    return False

def humanize_topic_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = cleaned.translate(_UNICODE_ROMAN_MAP)
    cleaned = cleaned.replace(",", ",")
    for pattern, replacement, flags in _LIGHT_SUBS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=flags)
    for pattern, replacement in _OCR_JOIN_SUBS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = _split_joined_function_word_suffixes(cleaned)
    cleaned = re.sub(
        r"\b([A-Za-z]{3,})\s+(ing|ion|ions|ive|ity|ment|ments|able|ance|ances|ness|tor|tors|tive|tic|ical|rical)\b",
        r"\1\2",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = _merge_fragmented_tokens(cleaned)
    cleaned = re.sub(r"\s*&\s*", " & ", cleaned)
    cleaned = re.sub(r"\s*\(\s*", " (", cleaned)
    cleaned = re.sub(r"\s*\)\s*", ") ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s*:\s*", ": ", cleaned)
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
    for source, target in _DIRECT_REPLACEMENTS:
        cleaned = re.sub(
            rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])",
            target,
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"\bLearning models\b", "learning models", cleaned)
    cleaned = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def split_period_topic_list(text: str) -> list[str]:
    candidate = text.strip(" .,-")
    if not candidate or candidate.count(".") < 1:
        return [candidate] if candidate else []

    parts = [p.strip(" .,-") for p in re.split(r"\s*\.\s*", candidate) if p.strip(" .,-")]
    if len(parts) <= 1:
        return [candidate]
    joined_without_spaces = bool(re.search(r"[A-Za-z]\.[A-Za-z]", candidate))
    if joined_without_spaces and len(parts) <= 8 and all(1 <= len(part.split()) <= 10 for part in parts):
        return parts
    if (
        len(parts) <= 8
        and all(1 <= len(part.split()) <= 10 for part in parts)
        and any(len(part.split()) >= 2 for part in parts)
    ):
        return parts
    return [candidate]


def topic_dedupe_key(text: str) -> str:
    normalized = _normalize_unit_prefixes(humanize_topic_text(text))
    return re.sub(r"[^a-z0-9]+", "", normalized.lower())
