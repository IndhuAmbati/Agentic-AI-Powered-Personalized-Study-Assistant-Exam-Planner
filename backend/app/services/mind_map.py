"""Build a lightweight study mind map from subjects and topics."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from backend.app.config import get_settings
from backend.app.models.subject import Subject
from backend.app.models.topic import Topic
from backend.app.services.topic_text import humanize_topic_text

_UNIT_PATTERN = re.compile(
    r"^\s*(Unit\s+[IVXLC\d]+)(?:\s*[:\-]\s*(.+))?$",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "about",
    "their",
    "this",
    "that",
    "unit",
    "topic",
    "topics",
    "theory",
    "introduction",
    "basics",
    "basic",
    "overview",
    "study",
}
_MIND_MAP_TEXT_VERSION = "v2"
_MIND_MAP_TEXT_CACHE: dict[str, str] = {}
_LAST_MIND_MAP_GROQ_ERROR = "Groq could not generate a topic overview right now."
_LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=None)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
logger = logging.getLogger(__name__)

def _safe_pct(value: float | int | None) -> float:
    pct = float(value or 0.0)
    return max(0.0, min(100.0, round(pct, 1)))


def _clean_words(text: str) -> list[str]:
    words: list[str] = []
    for match in _WORD_RE.findall(humanize_topic_text(text)):
        lowered = match.lower()
        if len(lowered) < 4 or lowered in _STOP_WORDS:
            continue
        words.append(match)
    return words


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = re.sub(r"[^a-z0-9]+", "", item.lower())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(item)
    return ordered


def _build_focus_points(unit_name: str, topic_labels: list[str]) -> list[str]:
    candidates = _clean_words(unit_name)
    for label in topic_labels:
        candidates.extend(_clean_words(label))
    return _dedupe_keep_order(candidates)[:4]


def _build_unit_headline(unit_name: str, topic_labels: list[str]) -> str:
    return ""


def _build_unit_learning_goal(unit_name: str, topic_labels: list[str]) -> str:
    return ""


def _build_unit_study_hint(topic_labels: list[str], completion_pct: float) -> str:
    return ""


def _build_subject_snapshot(subject_name: str, unit_cards: list[dict], topic_count: int) -> str:
    return ""


def _provider_config() -> tuple[str | None, str | None, str | None]:
    get_settings.cache_clear()
    settings = get_settings()
    if settings.groq_api_key:
        return (
            "https://api.groq.com/openai/v1/chat/completions",
            settings.groq_api_key,
            settings.groq_model,
        )
    return None, None, None


def _mind_map_cache_key(kind: str, subject_name: str, unit_name: str = "", topic_label: str = "") -> str:
    return " | ".join(
        [
            _MIND_MAP_TEXT_VERSION,
            kind,
            humanize_topic_text(subject_name).lower(),
            humanize_topic_text(unit_name).lower(),
            humanize_topic_text(topic_label).lower(),
        ]
    )


def _extract_json_payload(content: str) -> list[dict[str, str]]:
    raw = str(content or "").strip()
    if not raw:
        return []

    candidates = [raw]
    for match in _CODE_FENCE_RE.finditer(raw):
        block = match.group(1).strip()
        if block:
            candidates.append(block)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            start_obj = candidate.find("{")
            end_obj = candidate.rfind("}")
            if start_obj != -1 and end_obj > start_obj:
                try:
                    parsed = json.loads(candidate[start_obj : end_obj + 1])
                except json.JSONDecodeError:
                    start_arr = candidate.find("[")
                    end_arr = candidate.rfind("]")
                    if start_arr == -1 or end_arr <= start_arr:
                        continue
                    try:
                        parsed = json.loads(candidate[start_arr : end_arr + 1])
                    except json.JSONDecodeError:
                        continue
            else:
                start_arr = candidate.find("[")
                end_arr = candidate.rfind("]")
                if start_arr == -1 or end_arr <= start_arr:
                    continue
                try:
                    parsed = json.loads(candidate[start_arr : end_arr + 1])
                except json.JSONDecodeError:
                    continue

        items = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(items, list):
            continue

        rows: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            description = str(item.get("description") or "").strip()
            if key and description:
                rows.append({"key": key, "description": description})
        if rows:
            return rows
    return []


async def _generate_topic_descriptions_with_llm(items: list[dict[str, str]]) -> dict[str, str]:
    global _LAST_MIND_MAP_GROQ_ERROR
    base_url, api_key, model = _provider_config()
    if not base_url or not api_key or not model or not items:
        _LAST_MIND_MAP_GROQ_ERROR = "Groq is not configured for topic overviews."
        return {}

    system_prompt = (
        "You write clear, neat, student-friendly academic mini-explanations for a study mind map. "
        "Return only valid JSON. "
        "Do not repeat the topic title as the explanation. "
        "Explain the topic in simple theory-focused language. "
        "Keep each explanation concrete, specific, easy to scan, and naturally written."
    )
    user_prompt = (
        "For each topic below, return a JSON object with an `items` array. "
        'Each item must be {"key":"...", "description":"..."}.\n'
        "Rules:\n"
        "- Write 2 neat short sentences per description.\n"
        "- About 26 to 44 words total.\n"
        "- Write it like a small, clean theory overview of the topic, not study advice.\n"
        "- Explain the meaning first, then its purpose, use, or importance.\n"
        "- If `kind` is `subject`, describe what the subject broadly studies.\n"
        "- If `kind` is `unit`, describe the unit's theory focus and scope.\n"
        "- If `kind` is `topic`, describe the concept in a concise but clear academic way.\n"
        "- Mention a practical meaning or related idea only if it makes the explanation clearer.\n"
        "- Avoid generic phrasing and avoid simply repeating the title.\n"
        "- Avoid starting with phrases like 'This topic covers' or 'It is about'.\n"
        "- Prefer clean textbook-style wording that a student can understand quickly.\n"
        "- Keep the same key unchanged.\n\n"
        f"Topics:\n{json.dumps(items, ensure_ascii=True)}"
    )

    settings = get_settings()
    transport = (
        httpx.AsyncHTTPTransport(proxy=settings.groq_proxy_url)
        if settings.groq_proxy_url
        else None
    )
    response = None
    try:
        async with httpx.AsyncClient(
            timeout=_LLM_TIMEOUT,
            trust_env=not bool(settings.groq_proxy_url),
            transport=transport,
        ) as client:
            for attempt in range(3):
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
                        "temperature": 0.5,
                        "max_tokens": 1400,
                    },
                )
                if response.status_code == 429 and attempt < 2:
                    continue
                if response.status_code >= 500 and attempt < 2:
                    continue
                response.raise_for_status()
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not isinstance(content, str) or not content.strip():
                    continue
                parsed_rows = _extract_json_payload(content)
                if parsed_rows:
                    _LAST_MIND_MAP_GROQ_ERROR = ""
                    return {row["key"]: row["description"] for row in parsed_rows}
    except Exception as exc:  # pragma: no cover - network/provider failures
        body = ""
        try:
            body = response.text if response is not None else ""
        except Exception:
            body = ""
        detail = str(exc).strip() or "Unknown Groq error"
        if body:
            compact_body = re.sub(r"\s+", " ", body).strip()
            detail = f"{detail}: {compact_body[:240]}"
        _LAST_MIND_MAP_GROQ_ERROR = detail
        logger.warning("Groq mind map description generation failed: %s %s", exc, body[:240])
        return {}
    _LAST_MIND_MAP_GROQ_ERROR = "Groq returned no topic overview."
    return {}


async def enrich_mind_map_descriptions(payload: dict) -> dict:
    subjects = payload.get("subjects") or []
    pending_items: list[dict[str, str]] = []

    for subject in subjects:
        subject_name = str(subject.get("label") or "").strip()
        subject_key = _mind_map_cache_key("subject", subject_name)
        subject_cached = _MIND_MAP_TEXT_CACHE.get(subject_key)
        if subject_cached:
            subject["snapshot"] = subject_cached
        else:
            pending_items.append(
                {
                    "key": subject_key,
                    "kind": "subject",
                    "subject": subject_name,
                    "unit": "",
                    "topic": "",
                }
            )
        for unit in subject.get("units") or []:
            unit_name = str(unit.get("label") or "").strip()
            unit_key = _mind_map_cache_key("unit", subject_name, unit_name)
            unit_cached = _MIND_MAP_TEXT_CACHE.get(unit_key)
            if unit_cached:
                unit["headline"] = unit_cached
            else:
                pending_items.append(
                    {
                        "key": unit_key,
                        "kind": "unit",
                        "subject": subject_name,
                        "unit": unit_name,
                        "topic": "",
                    }
                )
            for topic in unit.get("topics") or []:
                topic_label = str(topic.get("label") or "").strip()
                cache_key = _mind_map_cache_key("topic", subject_name, unit_name, topic_label)
                cached = _MIND_MAP_TEXT_CACHE.get(cache_key)
                if cached:
                    topic["description"] = cached
                    continue
                pending_items.append(
                    {
                        "key": cache_key,
                        "kind": "topic",
                        "subject": subject_name,
                        "unit": unit_name,
                        "topic": topic_label,
                    }
                )

    if not pending_items:
        return payload

    for start in range(0, len(pending_items), 24):
        batch = pending_items[start : start + 24]
        generated = await _generate_topic_descriptions_with_llm(batch)
        for item in batch:
            description = generated.get(item["key"])
            if description:
                _MIND_MAP_TEXT_CACHE[item["key"]] = description

    for subject in subjects:
        subject_name = str(subject.get("label") or "").strip()
        subject_cached = _MIND_MAP_TEXT_CACHE.get(_mind_map_cache_key("subject", subject_name))
        if subject_cached:
            subject["snapshot"] = subject_cached
        for unit in subject.get("units") or []:
            unit_name = str(unit.get("label") or "").strip()
            unit_cached = _MIND_MAP_TEXT_CACHE.get(_mind_map_cache_key("unit", subject_name, unit_name))
            if unit_cached:
                unit["headline"] = unit_cached
            for topic in unit.get("topics") or []:
                topic_label = str(topic.get("label") or "").strip()
                cache_key = _mind_map_cache_key("topic", subject_name, unit_name, topic_label)
                cached = _MIND_MAP_TEXT_CACHE.get(cache_key)
                if cached:
                    topic["description"] = cached
    return payload


def apply_cached_mind_map_descriptions(payload: dict) -> dict:
    subjects = payload.get("subjects") or []
    for subject in subjects:
        subject_name = str(subject.get("label") or "").strip()
        subject_cached = _MIND_MAP_TEXT_CACHE.get(_mind_map_cache_key("subject", subject_name))
        if subject_cached:
            subject["snapshot"] = subject_cached
        for unit in subject.get("units") or []:
            unit_name = str(unit.get("label") or "").strip()
            unit_cached = _MIND_MAP_TEXT_CACHE.get(_mind_map_cache_key("unit", subject_name, unit_name))
            if unit_cached:
                unit["headline"] = unit_cached
            for topic in unit.get("topics") or []:
                topic_label = str(topic.get("label") or "").strip()
                topic_cached = _MIND_MAP_TEXT_CACHE.get(
                    _mind_map_cache_key("topic", subject_name, unit_name, topic_label)
                )
                if topic_cached:
                    topic["description"] = topic_cached
    return payload


async def generate_topic_description(
    *,
    subject_name: str,
    unit_name: str,
    topic_label: str,
) -> str:
    global _LAST_MIND_MAP_GROQ_ERROR
    cache_key = _mind_map_cache_key("topic", subject_name, unit_name, topic_label)
    cached = _MIND_MAP_TEXT_CACHE.get(cache_key)
    if cached:
        return cached

    generated = await _generate_topic_descriptions_with_llm(
        [
            {
                "key": cache_key,
                "kind": "topic",
                "subject": subject_name,
                "unit": unit_name,
                "topic": topic_label,
            }
        ]
    )
    description = generated.get(cache_key, "").strip()
    if description:
        _MIND_MAP_TEXT_CACHE[cache_key] = description
        _LAST_MIND_MAP_GROQ_ERROR = ""
        return description
    raise RuntimeError(_LAST_MIND_MAP_GROQ_ERROR or "Groq could not generate a topic overview right now.")


def _topic_group(name: str) -> tuple[str, str]:
    cleaned = humanize_topic_text(name)
    if not cleaned:
        return "Core Topics", "Untitled topic"

    unit_match = _UNIT_PATTERN.match(cleaned)
    if unit_match:
        unit_name = humanize_topic_text(unit_match.group(1))
        topic_label = humanize_topic_text(unit_match.group(2) or cleaned)
        if topic_label.lower() == unit_name.lower():
            topic_label = cleaned
        return unit_name, topic_label

    for separator in (":", " - "):
        if separator in cleaned:
            head, tail = cleaned.split(separator, 1)
            head = humanize_topic_text(head)
            tail = humanize_topic_text(tail)
            if head and tail and len(head.split()) <= 6:
                return head, tail

    return "Core Topics", cleaned


def build_mind_map(
    subjects: list[Subject],
    topics: list[Topic],
    weak_topic_stats: dict[str, dict[str, float | int]] | None = None,
) -> dict:
    """Return a nested graph payload tailored for the frontend mind map page."""
    weak_topic_stats = weak_topic_stats or {}
    topics_by_subject: dict[str, list[Topic]] = defaultdict(list)
    for topic in topics:
        topics_by_subject[topic.subject_id].append(topic)

    subject_cards = []
    all_topic_count = 0
    all_completed_count = 0
    node_count = 1  # root
    edge_count = 0

    for subject in subjects:
        subject_topics = sorted(
            topics_by_subject.get(subject.id, []),
            key=lambda item: (item.order_index, item.name.lower()),
        )
        grouped_topics: dict[str, list[Topic]] = defaultdict(list)
        topic_labels: dict[str, str] = {}

        for topic in subject_topics:
            unit_name, label = _topic_group(topic.name)
            grouped_topics[unit_name].append(topic)
            topic_labels[topic.id] = label

        unit_cards = []
        subject_completed = 0
        subject_weak_count = 0

        for unit_name, unit_topics in grouped_topics.items():
            ordered_topics = sorted(
                unit_topics,
                key=lambda item: (item.order_index, item.name.lower()),
            )
            unit_completed = sum(1 for topic in ordered_topics if topic.completed)
            topic_cards = []
            for topic in ordered_topics:
                weak_stat = weak_topic_stats.get(topic.id, {})
                weak_attempts = int(weak_stat.get("attempts", 0) or 0)
                weak_avg_score = round(float(weak_stat.get("average_score", 0.0) or 0.0), 1)
                is_weak = weak_attempts >= 3 and weak_avg_score < 75.0
                topic_cards.append(
                    {
                        "id": topic.id,
                        "label": topic_labels[topic.id],
                        "full_name": humanize_topic_text(topic.name),
                        "description": "",
                        "completion_pct": _safe_pct(topic.completion_pct),
                        "estimated_hours": round(float(topic.estimated_hours or 0.0), 1),
                        "difficulty": round(float(topic.difficulty or 0.0), 2),
                        "completed": bool(topic.completed),
                        "revision_count": int(topic.revision_count or 0),
                        "time_spent_mins": round(float(topic.time_spent_mins or 0.0), 1),
                        "order_index": int(topic.order_index or 0),
                        "is_weak": is_weak,
                        "weak_attempts": weak_attempts,
                        "weak_average_score": weak_avg_score,
                        "status_label": (
                            "Weak area"
                            if is_weak
                            else "Done"
                            if topic.completed
                            else "Strong progress"
                            if float(topic.completion_pct or 0.0) >= 60
                            else "Needs focus"
                        ),
                    }
                )
            unit_completion_pct = _safe_pct(
                (unit_completed / len(ordered_topics) * 100.0)
                if ordered_topics
                else 0.0
            )
            label_list = [topic["label"] for topic in topic_cards if topic.get("label")]
            weak_count = sum(1 for topic in topic_cards if topic.get("is_weak"))
            unit_cards.append(
                {
                    "id": f"{subject.id}:{unit_name}",
                    "label": unit_name,
                    "topic_count": len(ordered_topics),
                    "completed_topics": unit_completed,
                    "weak_topic_count": weak_count,
                    "completion_pct": unit_completion_pct,
                    "headline": _build_unit_headline(unit_name, label_list),
                    "learning_goal": _build_unit_learning_goal(unit_name, label_list),
                    "study_hint": _build_unit_study_hint(label_list, unit_completion_pct),
                    "focus_points": _build_focus_points(unit_name, label_list),
                    "topics": topic_cards,
                }
            )
            subject_completed += unit_completed
            subject_weak_count += weak_count

        topic_count = len(subject_topics)
        completion_pct = _safe_pct(
            (subject_completed / topic_count * 100.0) if topic_count else 0.0
        )

        subject_cards.append(
            {
                "id": subject.id,
                "label": humanize_topic_text(subject.name),
                "color": subject.color or "#4A90D9",
                "exam_date": subject.exam_date.isoformat() if subject.exam_date else None,
                "priority": round(float(subject.priority or 0.0), 1),
                "topic_count": topic_count,
                "completed_topics": subject_completed,
                "weak_topic_count": subject_weak_count,
                "completion_pct": completion_pct,
                "snapshot": _build_subject_snapshot(
                    humanize_topic_text(subject.name),
                    unit_cards,
                    topic_count,
                ),
                "units": unit_cards,
            }
        )

        all_topic_count += topic_count
        all_completed_count += subject_completed
        node_count += 1 + len(unit_cards) + topic_count
        edge_count += len(unit_cards) + topic_count

    overall_completion = _safe_pct(
        (all_completed_count / all_topic_count * 100.0) if all_topic_count else 0.0
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "subject_count": len(subjects),
        "topic_count": all_topic_count,
        "completed_topics": all_completed_count,
        "overall_completion_pct": overall_completion,
        "node_count": node_count if subjects else 0,
        "edge_count": edge_count,
        "root": {
            "id": "study-root",
            "label": "My Study Map",
            "summary": "Subjects branch into units and topics so you can spot coverage and weak areas quickly.",
        },
        "subjects": subject_cards,
    }
