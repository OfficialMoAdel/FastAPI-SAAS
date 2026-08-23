"""Event priority levels."""

from enum import Enum


class EventPriority(str, Enum):
    """Event priority levels."""

    CRITICAL = "CRITICAL"
    NORMAL = "NORMAL"
    LOW = "LOW"
