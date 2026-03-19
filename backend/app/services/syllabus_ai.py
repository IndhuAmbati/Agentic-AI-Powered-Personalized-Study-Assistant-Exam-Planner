"""LLM-assisted syllabus extraction from OCR text."""

from __future__ import annotations

import json
import re

import httpx

from backend.app.config import get_settings


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _provider_config() -> tuple[str | None, str | None, str | None]:
    get_settings.cache_clear()
    settings = get_settings()
    provider = (settings.ai_provider or "").lower()
    if provider == "openai" and settings.openai_api_key:
        return (
            "https://api.openai.com/v1/chat/completions",
            settings.openai_api_key,
            "gpt-4o-mini",
        )
    if provider == "groq" and settings.groq_api_key:
        return (
            "https://api.groq.com/openai/v1/chat/completions",
            settings.groq_api_key,
            settings.groq_model,
        )
    return None, None, None


def _truncate_text(text: str, limit: int = 18000) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return compact[:limit]


def _extract_json(content: str) -> dict[str, object] | None:
    raw = (content or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _normalize_subjects(payload: dict[str, object]) -> list[dict[str, list[str]]]:
    subjects = payload.get("subjects")
    if not isinstance(subjects, list):
        return []

    normalized: list[dict[str, list[str]]] = []
    for item in subjects:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        raw_topics = item.get("topics") or []
        if not name or not isinstance(raw_topics, list):
            continue
        topics = [str(topic or "").strip() for topic in raw_topics if str(topic or "").strip()]
        if not topics:
            continue
        normalized.append({"name": name, "topics": topics})
    return normalized


def _count_distinct_units(subjects: list[dict[str, list[str]]]) -> int:
    units: set[int] = set()
    for subject in subjects:
        for topic in subject.get("topics", []):
            match = re.match(r"^\s*Unit\s+(\d+)\b", str(topic), re.IGNORECASE)
            if match:
                units.add(int(match.group(1)))
    return len(units)


def looks_too_sparse_for_schedule(
    subjects: list[dict[str, list[str]]],
    *,
    unit_start: int = 1,
    unit_end: int = 5,
) -> bool:
    """Reject AI output that is too summarized to build a useful long schedule."""
    if not subjects:
        return True
    total_topics = sum(len(subject.get("topics", [])) for subject in subjects)
    distinct_units = _count_distinct_units(subjects)
    expected_units = max(unit_end - unit_start + 1, 1)
    if total_topics < max(8, expected_units + 2):
        return True
    if distinct_units < min(expected_units, 3):
        return True
    return False


async def extract_subjects_and_topics_with_llm(
    text: str,
    *,
    unit_start: int = 1,
    unit_end: int = 5,
) -> list[dict[str, list[str]]]:
    """Ask the configured LLM to return subjects and only the requested unit topics."""
    base_url, api_key, model = _provider_config()
    if not base_url or not api_key or not model:
        return []

    prompt_text = _truncate_text(text)
    if not prompt_text:
        return []

    system_prompt = (
        "You extract clean syllabus data from noisy OCR text. "
        "Return only valid JSON. "
        "Identify each subject/course name and include only topics that belong to the requested unit range. "
        "Extract every individual topic or subtopic you can find under those units, not just one summary heading for a unit. "
        "Ignore course outcomes, textbooks, references, author names, metadata, credit tables, exam schemes, prerequisites, and publisher names. "
        "Keep topic text concise and readable. "
        "Drop broken OCR fragments, incomplete trailing phrases, citation lines, and standalone words that are not meaningful syllabus topics. "
        "If a topic belongs to Unit N, output it as 'Unit N: topic name'. "
        "Do not invent topics."
    )
    user_prompt = (
        f"From the OCR text below, extract subjects and topics only for Unit {unit_start} to Unit {unit_end}.\n"
        "Return JSON exactly in this shape:\n"
        '{\n  "subjects": [\n    {"name": "Subject Name", "topics": ["Unit 1: Topic A", "Unit 1: Topic B"]}\n  ]\n}\n'
        "Rules:\n"
        f"- Keep only Unit {unit_start} to Unit {unit_end} topics.\n"
        "- Do not collapse an entire unit into a single line if the OCR text lists multiple topics under that unit.\n"
        "- Prefer many specific topics/subtopics instead of 1 broad unit title.\n"
        "- Do not include Unit 6+, outcomes, references, authors, syllabus headers, or junk OCR fragments.\n"
        "- Exclude incomplete topics such as cut-off phrases, standalone author names, publisher text, or trailing words like 'Application', 'First', 'Second', 'Finite' unless the OCR clearly shows the full topic.\n"
        "- If multiple subjects exist, group topics under the correct subject.\n"
        "- If subject names include course codes, keep them.\n\n"
        f"OCR text:\n{prompt_text}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
        response.raise_for_status()
        data = response.json()
        content = str(data["choices"][0]["message"]["content"] or "")
    except Exception:
        return []

    payload = _extract_json(content)
    if not payload:
        return []
    return _normalize_subjects(payload)
