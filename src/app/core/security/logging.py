"""Security event logging schema and utilities.

Provides standardized structured logging for all security events across
the application to enable consistent monitoring, alerting, and forensic analysis.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.app.core.json_utils import json_dumps
from src.app.core.logger import get_logger


class SecurityEventType(str, Enum):
    """Enumeration of all security event types.

    Covers all security features in the application for consistent categorization.
    """

    IP_BLOCKED = "ip_blocked"
    """IP address was blocked by blacklist."""

    IP_WHITELISTED = "ip_whitelisted"
    """IP address was allowed by whitelist."""

    ATTACK_DETECTED = "attack_detected"
    """Suspicious request pattern detected (SQL injection, XSS, etc.)."""

    RATE_LIMITED = "rate_limited"
    """Client exceeded rate limit."""

    AUTO_BANNED = "auto_banned"
    """Client automatically banned after repeated violations."""

    BAD_BOT_BLOCKED = "bad_bot_blocked"
    """Known bad bot/scanner user agent blocked."""

    AUTHENTICATION_FAILURE = "authentication_failure"
    """Failed authentication attempt."""

    AUTHORIZATION_FAILURE = "authorization_failure"
    """Insufficient permissions for operation."""

    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    """General suspicious activity detected."""

    TOKEN_BLACKLISTED = "token_blacklisted"
    """JWT token was blacklisted/revoked."""


@dataclass(frozen=True)
class SecurityEvent:
    """Immutable data structure for security events.

    All security middleware should use this class to log security events
    for consistent, parseable output suitable for monitoring and alerting.

    Attributes:
        event_type: Type of security event (from SecurityEventType enum)
        client_ip: Client IP address that triggered the event
        request_path: Request path/URL that triggered the event
        reason: Human-readable explanation of why the event occurred
        pattern_matched: Optional pattern/rule that was matched (e.g., regex, rule ID)
        user_agent: Optional client user agent string
        correlation_id: Optional request correlation ID for tracing
        extra: Optional additional context-specific data
        timestamp: Event timestamp (auto-generated)
    """

    event_type: SecurityEventType
    """Type of security event (from SecurityEventType enum)."""

    client_ip: str
    """Client IP address that triggered the event."""

    request_path: str
    """Request path/URL that triggered the event."""

    reason: str
    """Human-readable explanation of why the event occurred."""

    pattern_matched: str | None = None
    """Optional pattern/rule that was matched (e.g., regex, rule ID)."""

    user_agent: str | None = None
    """Optional client user agent string."""

    correlation_id: str | None = None
    """Optional request correlation ID for distributed tracing."""

    extra: dict[str, Any] | None = field(default=None)
    """Optional additional context-specific data."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    """Event timestamp (auto-generated in UTC)."""

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the security event
        """
        return {
            "event_type": self.event_type.value,
            "client_ip": self.client_ip,
            "request_path": self.request_path,
            "reason": self.reason,
            "pattern_matched": self.pattern_matched,
            "user_agent": self.user_agent,
            "correlation_id": self.correlation_id,
            "extra": self.extra,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_json(self) -> str:
        """Convert event to JSON string.

        Returns:
            JSON string representation of the security event
        """
        return json_dumps(self.to_dict(), default=str, as_str=True)


# Security event logger instance
security_logger = get_logger(__name__)


def log_security_event(
    event: SecurityEvent,
    level: int = logging.WARNING,
) -> None:
    """Log security event in structured format.

    Outputs structured JSON-compatible logs that can be ingested by
    log aggregation systems (ELK, Splunk, Datadog, etc.) for monitoring
    and alerting.

    Args:
        event: The security event to log
        level: Logging level (default: WARNING for security events)

    Example:
        >>> event = SecurityEvent(
        ...     event_type=SecurityEventType.IP_BLOCKED,
        ...     client_ip="192.168.1.100",
        ...     request_path="/api/admin",
        ...     reason="IP found in blacklist",
        ... )
        >>> log_security_event(event)

    Output format:
        {
            "event": "security_event",
            "event_type": "ip_blocked",
            "client_ip": "192.168.1.100",
            "request_path": "/api/admin",
            "reason": "IP found in blacklist",
            "timestamp": "2024-01-15T10:30:00+00:00"
        }
    """
    log_data = {
        "event": "security_event",
        **event.to_dict(),
    }

    # Log as JSON string for structured logging compatibility
    security_logger.log(level, json_dumps(log_data, default=str, as_str=True))


def create_security_event(
    event_type: SecurityEventType,
    client_ip: str,
    request_path: str,
    reason: str,
    pattern_matched: str | None = None,
    user_agent: str | None = None,
    correlation_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> SecurityEvent:
    """Factory function to create a SecurityEvent with validation.

    Provides a convenient way to create security events with proper validation.

    Args:
        event_type: Type of security event
        client_ip: Client IP address
        request_path: Request path that triggered the event
        reason: Explanation of why the event occurred
        pattern_matched: Optional pattern/rule that was matched
        user_agent: Optional client user agent string
        correlation_id: Optional request correlation ID
        extra: Optional additional context-specific data

    Returns:
        Validated SecurityEvent instance

    Example:
        >>> event = create_security_event(
        ...     event_type=SecurityEventType.RATE_LIMITED,
        ...     client_ip="10.0.0.1",
        ...     request_path="/api/login",
        ...     reason="Exceeded 5 requests per minute",
        ...     extra={"limit": 5, "period": 60},
        ... )
    """
    return SecurityEvent(
        event_type=event_type,
        client_ip=client_ip,
        request_path=request_path,
        reason=reason,
        pattern_matched=pattern_matched,
        user_agent=user_agent,
        correlation_id=correlation_id,
        extra=extra,
    )
