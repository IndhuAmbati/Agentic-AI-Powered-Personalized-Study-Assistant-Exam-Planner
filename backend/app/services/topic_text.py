"""Shared text normalization helpers for OCR-derived topic strings."""

from __future__ import annotations

import re

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
    cleaned = str(text or "").translate(_UNICODE_ROMAN_MAP)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s*&\s*", " & ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s*:\s*", ": ", cleaned)
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
    cleaned = re.sub(r"\s*\(\s*", " (", cleaned)
    cleaned = re.sub(r"\s*\)\s*", ") ", cleaned)
    cleaned = _normalize_unit_prefixes(cleaned)
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
    if len(parts) <= 8 and all(1 <= len(part.split()) <= 10 for part in parts) and any(len(part.split()) >= 2 for part in parts):
        return parts
    return [candidate]


def topic_dedupe_key(text: str) -> str:
    normalized = _normalize_unit_prefixes(humanize_topic_text(text))
    return re.sub(r"[^a-z0-9]+", "", normalized.lower())
