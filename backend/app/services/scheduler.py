"""
Core Scheduling Engine
======================
Priority-based heuristic scheduler that allocates study blocks across topics.

Priority Score Formula
----------------------
For each topic *t* belonging to subject *s*:

    priority(t) = w_exam  * exam_urgency(s)
                + w_diff  * difficulty(t)
                + w_prog  * (1 - completion_pct(t) / 100)
                + w_rev   * revision_need(t)

Where:
  - exam_urgency ∈ [0,1]: higher when fewer days remain until the exam.
  - difficulty ∈ [0,1]: from topic metadata.
  - completion gap: inverse of % done — incomplete topics rank higher.
  - revision_need ∈ [0,1]: spaced-repetition signal based on last review time.

Weights default to (0.35, 0.20, 0.30, 0.15) and are tunable.
"""

from __future__ import annotations

import math
import re
from datetime import date, time, datetime, timedelta
from dataclasses import dataclass, field

from backend.app.models.topic import Topic
from backend.app.models.subject import Subject
from backend.app.models.schedule import ScheduleEntry


# ── Configuration defaults ──────────────────────────────────────────
W_EXAM = 0.35
W_DIFF = 0.20
W_PROG = 0.30
W_REV  = 0.15

REVISION_INTERVAL_DAYS = [1, 3, 7, 14, 30]  # spaced-repetition intervals
UNIT_TAG_PATTERN = re.compile(r"\bunit\s+([ivxlcdm\d]+)\b", re.IGNORECASE)


# ── Helper data classes ─────────────────────────────────────────────

@dataclass
class TopicPriority:
    """Intermediate structure used while scoring topics."""
    topic: Topic
    subject: Subject
    priority_score: float = 0.0
    is_revision: bool = False


@dataclass
class ScheduleBlock:
    """A scheduled study block before it becomes a DB row."""
    topic_id: str
    subject_name: str
    topic_name: str
    scheduled_date: date
    start_time: time
    end_time: time
    duration_mins: float
    priority_score: float
    is_revision: bool = False


# ── Public API ──────────────────────────────────────────────────────

def compute_priority(
    topic: Topic,
    subject: Subject,
    reference_date: date | None = None,
    weights: tuple[float, float, float, float] = (W_EXAM, W_DIFF, W_PROG, W_REV),
) -> TopicPriority:
    """Return a :class:`TopicPriority` for a single topic."""
    ref = reference_date or date.today()
    w_exam, w_diff, w_prog, w_rev = weights

    # 1. Exam urgency
    if subject.exam_date:
        days_left = max((subject.exam_date - ref).days, 1)
        exam_urgency = 1.0 / (1.0 + math.log1p(days_left))
    else:
        exam_urgency = 0.3  # no exam date → moderate urgency

    # 2. Difficulty
    difficulty = topic.difficulty  # already 0–1

    # 3. Completion gap
    completion_gap = 1.0 - (topic.completion_pct / 100.0)

    # 4. Revision need (spaced repetition)
    revision_need = 0.0
    is_revision = False
    if topic.completed and topic.last_reviewed:
        days_since = (datetime.now(tz=topic.last_reviewed.tzinfo) - topic.last_reviewed).days
        for interval in REVISION_INTERVAL_DAYS:
            if days_since >= interval:
                revision_need = min(1.0, days_since / 30.0)
                is_revision = True
                break

    score = (
        w_exam * exam_urgency
        + w_diff * difficulty
        + w_prog * completion_gap
        + w_rev * revision_need
    )

    # Boost by subject-level priority (user-defined 0-5 → 0-1 normalised)
    score *= (0.5 + subject.priority / 10.0)

    return TopicPriority(topic=topic, subject=subject, priority_score=round(score, 4), is_revision=is_revision)


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
    match = UNIT_TAG_PATTERN.search(name or "")
    return _roman_to_int(match.group(1)) if match else None


def _topic_order_key(topic: Topic) -> tuple[int, int, str]:
    unit_number = _topic_unit_number(topic.name)
    unit_sort = unit_number if unit_number is not None else 10**6
    return (unit_sort, int(topic.order_index or 0), str(topic.name or "").lower())


def _clean_display_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = re.sub(r"\bPer\s+for\s+mances\b", "Performances", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bPro\s+to\s+cols\b", "Protocols", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bMoni\s+to\s+ring\b", "Monitoring", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bPiwith\b", "Pi with", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwithbasic\b", "with basic", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bbasicperipherals\b", "basic peripherals", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,-")


def generate_schedule_rule_based(
    user_id: str,
    subjects: list[Subject],
    start_date: date,
    end_date: date,
    daily_hours: float = 4.0,
    daily_start: time = time(8, 0),
    session_mins: int = 60,
    break_mins: int = 15,
    max_topics_per_day: int | None = None,
    distribute_across_range: bool = True,
    split_long_topics: bool = False,
    ensure_full_coverage: bool = False,
) -> list[ScheduleBlock]:
    """Deterministic, no-AI scheduler: Unit-wise, round-robin, and hour-aware."""
    if not subjects:
        return []

    subject_order = {subj.id: idx for idx, subj in enumerate(subjects)}
    unit_subject_topics: dict[int, dict[str, list[dict[str, object]]]] = {}
    no_unit_subject_topics: dict[str, list[dict[str, object]]] = {}

    def _sessions_required(topic: Topic) -> int:
        if not split_long_topics:
            return 1
        remaining_mins = max(topic.estimated_hours * 60 - topic.time_spent_mins, session_mins)
        return max(1, math.ceil(remaining_mins / max(session_mins, 1)))

    for subj in subjects:
        sorted_topics = sorted(subj.topics, key=_topic_order_key)
        for topic in sorted_topics:
            required = _sessions_required(topic)
            topic_state = {
                "subject": subj,
                "topic": topic,
                "remaining": required,
                "total": required,
            }
            unit_no = _topic_unit_number(topic.name)
            if unit_no is None:
                no_unit_subject_topics.setdefault(subj.id, []).append(topic_state)
            else:
                unit_subject_topics.setdefault(unit_no, {}).setdefault(subj.id, []).append(topic_state)

    ordered_sessions: list[tuple[Subject, Topic, int, int]] = []
    for unit_no in sorted(unit_subject_topics.keys()):
        buckets = unit_subject_topics[unit_no]
        subj_ids = sorted(buckets.keys(), key=lambda sid: subject_order.get(sid, 10**6))
        while True:
            added = False
            for sid in subj_ids:
                bucket = buckets[sid]
                if bucket:
                    current = bucket[0]
                    subject = current["subject"]
                    topic = current["topic"]
                    total = int(current["total"])
                    completed = total - int(current["remaining"]) + 1
                    ordered_sessions.append((subject, topic, completed, total))
                    current["remaining"] = int(current["remaining"]) - 1
                    if int(current["remaining"]) <= 0:
                        bucket.pop(0)
                    added = True
            if not added:
                break

    if no_unit_subject_topics:
        subj_ids = sorted(no_unit_subject_topics.keys(), key=lambda sid: subject_order.get(sid, 10**6))
        while True:
            added = False
            for sid in subj_ids:
                bucket = no_unit_subject_topics[sid]
                if bucket:
                    current = bucket[0]
                    subject = current["subject"]
                    topic = current["topic"]
                    total = int(current["total"])
                    completed = total - int(current["remaining"]) + 1
                    ordered_sessions.append((subject, topic, completed, total))
                    current["remaining"] = int(current["remaining"]) - 1
                    if int(current["remaining"]) <= 0:
                        bucket.pop(0)
                    added = True
            if not added:
                break

    if not ordered_sessions:
        return []

    schedule: list[ScheduleBlock] = []
    current_date = start_date
    queue_idx = 0
    total = len(ordered_sessions)

    while queue_idx < total and (current_date <= end_date or ensure_full_coverage):
        # Interpret daily_hours as pure study time. Breaks are added between sessions.
        slots_today = int((daily_hours * 60) // max(session_mins, 1))
        if max_topics_per_day is not None and max_topics_per_day > 0:
            slots_today = min(slots_today, max_topics_per_day)
        if distribute_across_range and current_date <= end_date:
            remaining_days = max((end_date - current_date).days + 1, 1)
            pending = total - queue_idx
            slots_today = min(slots_today, max(1, math.ceil(pending / remaining_days)))

        cursor = datetime.combine(current_date, daily_start)
        for _ in range(max(slots_today, 0)):
            if queue_idx >= total:
                break
            subj, topic, part_idx, part_total = ordered_sessions[queue_idx]
            block_end = cursor + timedelta(minutes=session_mins)
            priority = round(max(0.1, 1.0 - (queue_idx / max(total, 1))), 4)
            topic_name = topic.name
            if split_long_topics and part_total > 1:
                topic_name = f"{topic_name} (Part {part_idx}/{part_total})"
            schedule.append(
                ScheduleBlock(
                    topic_id=topic.id,
                    subject_name=_clean_display_text(subj.name),
                    topic_name=_clean_display_text(topic_name),
                    scheduled_date=current_date,
                    start_time=cursor.time(),
                    end_time=block_end.time(),
                    duration_mins=session_mins,
                    priority_score=priority,
                    is_revision=False,
                )
            )
            queue_idx += 1
            cursor = block_end + timedelta(minutes=break_mins)

        current_date += timedelta(days=1)

    return schedule


def generate_schedule(
    user_id: str,
    subjects: list[Subject],
    start_date: date,
    end_date: date,
    daily_hours: float = 4.0,
    daily_start: time = time(8, 0),
    session_mins: int = 60,
    break_mins: int = 15,
    max_topics_per_day: int | None = None,
    avoid_topic_repeats: bool = False,
    enforce_unit_sequence: bool = False,
    distribute_across_range: bool = False,
    ensure_full_coverage: bool = False,
) -> list[ScheduleBlock]:
    """
    Build a study schedule spanning *start_date* → *end_date*.

    Algorithm
    ---------
    1. Score every topic.
    2. For each day, fill available time slots by picking the highest-priority
       topics first, respecting session duration and breaks.
    3. After scheduling a session, reduce the topic's remaining estimate so
       subsequent days don't over-allocate.

    Returns a list of :class:`ScheduleBlock` objects ready to persist.
    """
    # Flatten all topics with priorities
    topic_priorities: list[TopicPriority] = []
    for subj in subjects:
        for topic in subj.topics:
            tp = compute_priority(topic, subj, reference_date=start_date)
            topic_priorities.append(tp)

    if not topic_priorities:
        return []

    # Track remaining estimated time per topic (minutes)
    remaining: dict[str, float] = {}
    for tp in topic_priorities:
        raw_remaining = max(
            tp.topic.estimated_hours * 60 - tp.topic.time_spent_mins,
            session_mins,
        )
        remaining[tp.topic.id] = raw_remaining

    schedule: list[ScheduleBlock] = []
    current_date = start_date
    scheduled_once: set[str] = set()

    def _has_pending_time() -> bool:
        return any(minutes > 0 for minutes in remaining.values())

    while current_date <= end_date or (ensure_full_coverage and _has_pending_time()):
        # Re-sort by priority (may change as dates shift)
        for tp in topic_priorities:
            tp.priority_score = compute_priority(
                tp.topic, tp.subject, reference_date=current_date
            ).priority_score

        topic_priorities.sort(key=lambda tp: tp.priority_score, reverse=True)

        # Interpret daily_hours as pure study time. Breaks are added between sessions.
        slots_today = int((daily_hours * 60) // max(session_mins, 1))
        if max_topics_per_day is not None and max_topics_per_day > 0:
            slots_today = min(slots_today, max_topics_per_day)
        if distribute_across_range and avoid_topic_repeats and current_date <= end_date:
            remaining_days = max((end_date - current_date).days + 1, 1)
            pending_topics = sum(
                1
                for tp in topic_priorities
                if tp.topic.id not in scheduled_once and remaining.get(tp.topic.id, 0) > 0
            )
            if pending_topics > 0:
                slots_today = min(
                    slots_today,
                    max(1, math.ceil(pending_topics / remaining_days)),
                )
        cursor = datetime.combine(current_date, daily_start)

        scheduled_today: set[str] = set()  # avoid same topic twice in a day
        scheduled_subjects_today: set[str] = set()  # spread slots across subjects when possible
        scheduled_units: set[str] = set()  # spread across units if topic names include unit tags

        subject_order = {subj.id: idx for idx, subj in enumerate(subjects)}

        def _unit_key(name: str) -> str:
            match = UNIT_TAG_PATTERN.search(name or "")
            return match.group(1).upper() if match else ""

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

        def _unit_number(name: str) -> int | None:
            key = _unit_key(name)
            return _roman_to_int(key) if key else None

        for _ in range(slots_today):
            # Pick the best unscheduled topic that still needs time
            chosen: TopicPriority | None = None
            prefer_unseen_topics = avoid_topic_repeats and any(
                tp.topic.id not in scheduled_once and remaining.get(tp.topic.id, 0) > 0
                for tp in topic_priorities
            )

            def _candidate_allowed(tp: TopicPriority) -> bool:
                if tp.topic.id in scheduled_today:
                    return False
                if remaining.get(tp.topic.id, 0) <= 0:
                    return False
                if prefer_unseen_topics and tp.topic.id in scheduled_once:
                    return False
                return True

            if enforce_unit_sequence:
                pending_units = sorted(
                    {
                        unit_no
                        for tp in topic_priorities
                        for unit_no in [_unit_number(tp.topic.name)]
                        if (
                            unit_no is not None
                            and (not prefer_unseen_topics or tp.topic.id not in scheduled_once)
                            and remaining.get(tp.topic.id, 0) > 0
                        )
                    }
                )

                target_unit = pending_units[0] if pending_units else None
                if target_unit is not None:
                    unit_candidates = [
                        tp
                        for tp in topic_priorities
                        if (
                            _candidate_allowed(tp)
                            and _unit_number(tp.topic.name) == target_unit
                        )
                    ]
                    unit_candidates.sort(
                        key=lambda tp: (
                            subject_order.get(tp.subject.id, 10**6),
                            int(tp.topic.order_index or 0),
                            str(tp.topic.name or "").lower(),
                            -tp.priority_score,
                        )
                    )
                    fresh_subject_candidates = [
                        tp for tp in unit_candidates if tp.subject.id not in scheduled_subjects_today
                    ]
                    chosen = (
                        fresh_subject_candidates[0]
                        if fresh_subject_candidates
                        else (unit_candidates[0] if unit_candidates else None)
                    )
                else:
                    no_unit_candidates = [
                        tp
                        for tp in topic_priorities
                        if (
                            _candidate_allowed(tp)
                            and _unit_number(tp.topic.name) is None
                        )
                    ]
                    no_unit_candidates.sort(
                        key=lambda tp: (
                            subject_order.get(tp.subject.id, 10**6),
                            int(tp.topic.order_index or 0),
                            str(tp.topic.name or "").lower(),
                            -tp.priority_score,
                        )
                    )
                    fresh_subject_candidates = [
                        tp for tp in no_unit_candidates if tp.subject.id not in scheduled_subjects_today
                    ]
                    chosen = (
                        fresh_subject_candidates[0]
                        if fresh_subject_candidates
                        else (no_unit_candidates[0] if no_unit_candidates else None)
                    )
            else:
                for tp in topic_priorities:
                    unit_key = _unit_key(tp.topic.name)
                    if (
                        _candidate_allowed(tp)
                        and unit_key
                        and unit_key not in scheduled_units
                    ):
                        chosen = tp
                        break

            if chosen is None:
                # Fallback: allow topics without explicit unit tag while still avoiding duplicates
                for tp in topic_priorities:
                    if (
                        _candidate_allowed(tp)
                    ):
                        chosen = tp
                        break

            if chosen is None:
                # Once every topic has been seen at least once, allow repeats on later days
                # until the estimated study time is exhausted.
                for tp in topic_priorities:
                    if tp.topic.id not in scheduled_today and remaining.get(tp.topic.id, 0) > 0:
                        chosen = tp
                        break

            if chosen is None:
                break  # all topics fully scheduled

            block_end = cursor + timedelta(minutes=session_mins)
            block = ScheduleBlock(
                topic_id=chosen.topic.id,
                subject_name=_clean_display_text(chosen.subject.name),
                topic_name=_clean_display_text(chosen.topic.name),
                scheduled_date=current_date,
                start_time=cursor.time(),
                end_time=block_end.time(),
                duration_mins=session_mins,
                priority_score=chosen.priority_score,
                is_revision=chosen.is_revision,
            )
            schedule.append(block)
            remaining[chosen.topic.id] = max(remaining[chosen.topic.id] - session_mins, 0)
            scheduled_today.add(chosen.topic.id)
            scheduled_once.add(chosen.topic.id)
            scheduled_subjects_today.add(chosen.subject.id)
            chosen_unit = _unit_key(chosen.topic.name)
            if chosen_unit:
                scheduled_units.add(chosen_unit)

            cursor = block_end + timedelta(minutes=break_mins)

        current_date += timedelta(days=1)

    return schedule


def blocks_to_entries(user_id: str, blocks: list[ScheduleBlock]) -> list[ScheduleEntry]:
    """Convert :class:`ScheduleBlock` objects into ORM :class:`ScheduleEntry` rows."""
    entries: list[ScheduleEntry] = []
    for b in blocks:
        entry = ScheduleEntry(
            user_id=user_id,
            topic_id=b.topic_id,
            subject_name=b.subject_name,
            topic_name=b.topic_name,
            scheduled_date=b.scheduled_date,
            start_time=b.start_time,
            end_time=b.end_time,
            duration_mins=b.duration_mins,
            priority_score=b.priority_score,
            is_revision=1 if b.is_revision else 0,
            completed=0,
        )
        entries.append(entry)
    return entries
