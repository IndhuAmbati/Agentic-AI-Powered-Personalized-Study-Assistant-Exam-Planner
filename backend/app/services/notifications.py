"""
Notification Service
====================
Simple in-app / logging-based notification system.
Can be extended with email (SMTP), push (Firebase), or webhook integrations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# In-memory store (replace with DB or message queue in production)
_notifications: list[dict] = []


@dataclass
class Notification:
    user_id: str
    title: str
    body: str
    channel: str = "app"  # app | email | push
    read: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def send_notification(user_id: str, title: str, body: str, channel: str = "app") -> dict:
    """Create and store a notification."""
    n = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "channel": channel,
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _notifications.append(n)
    logger.info("Notification [%s] → %s: %s", channel, user_id, title)
    return n


def get_notifications(user_id: str, unread_only: bool = False) -> list[dict]:
    """Return notifications for a user."""
    result = [n for n in _notifications if n["user_id"] == user_id]
    if unread_only:
        result = [n for n in result if not n["read"]]
    return result


def mark_read(user_id: str) -> int:
    """Mark all notifications as read for a user. Returns count."""
    count = 0
    for n in _notifications:
        if n["user_id"] == user_id and not n["read"]:
            n["read"] = True
            count += 1
    return count
