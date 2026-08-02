"""
Log sanitizer module for removing sensitive data from logs.

This module provides comprehensive sanitization of sensitive data in log messages,
including credentials, tokens, API keys, PII, and database connection strings.
"""

import logging
import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any


@dataclass
class SanitizationRule:
    """Rule for sanitizing sensitive data in logs."""

    pattern: str  # Compiled regex pattern
    replacement: str  # Replacement string
    description: str | None = None


class LogSanitizer:
    """
    Sanitizes sensitive data from log messages.

    Pre-compiles all regex patterns for performance.
    Handles strings, dicts, lists, and nested structures.
    """

    # Pre-compiled patterns for performance
    SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
        # JWT tokens
        (
            re.compile(r"eyJ[A-Za-z0-9-_]+\.eyJ[A-Za-z0-9-_]+\.[A-Za-z0-9-_]*"),
            "[JWT_TOKEN]",
        ),
        # API keys (key-value)
        (
            re.compile(r"(?i)(api[_-]?key|apikey)['\"]?\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"),
            r"\1=[REDACTED]",
        ),
        # Secrets/tokens/passwords
        (
            re.compile(r"(?i)(secret|token|password|passwd|pwd)['\"]?\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"),
            r"\1=[REDACTED]",
        ),
        # PostgreSQL URLs (sync and async)
        (
            re.compile(r"postgresql(\+asyncpg)?://[^:]+:[^@]+@"),
            r"postgresql\1://[USER]:[REDACTED]@",
        ),
        # MySQL URLs
        (
            re.compile(r"mysql(\+[a-z]+)?://[^:]+:[^@]+@"),
            r"mysql\1://[USER]:[REDACTED]@",
        ),
        # Redis URLs
        (
            re.compile(r"redis://[^:]*:[^@]+@"),
            "redis://[USER]:[REDACTED]@",
        ),
        # MongoDB URLs
        (
            re.compile(r"mongodb(\+srv)?://[^:]+:[^@]+@"),
            r"mongodb\1://[USER]:[REDACTED]@",
        ),
        # Email addresses (PARTIAL MASK - preserve domain)
        (
            re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b"),
            r"\1***\2",
        ),
        # Credit card numbers
        (
            re.compile(r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
            "[CARD_NUMBER]",
        ),
        # SSN (US)
        (
            re.compile(r"\b\d{3}[\s-]?\d{2}[\s-]?\d{4}\b"),
            "[SSN]",
        ),
        # AWS Access Keys
        (
            re.compile(r"AKIA[0-9A-Z]{16}"),
            "[AWS_ACCESS_KEY]",
        ),
        # AWS Secret Keys
        (
            re.compile(r"(?i)(aws_secret[_]?access[_]?key)['\"]?\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?"),
            r"\1=[REDACTED]",
        ),
        # Stripe Keys
        (
            re.compile(r"(sk|pk|rk)_(live|test)_[A-Za-z0-9]{24,}"),
            "[STRIPE_KEY]",
        ),
        # Bearer tokens in Authorization headers
        (
            re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_\.]+"),
            r"\1[REDACTED]",
        ),
        # Generic Authorization header
        (
            re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"),
            r"\1[REDACTED]",
        ),
        # WebAuthn / Passkey sensitive data
        (
            re.compile(
                r"(?i)(challenge|credential_id|public_key|signature|attestation_object|client_data_json|authenticator_data|user_handle)['\"]?\s*[:=]\s*['\"]?([^'\"\s,}]+)['\"]?"
            ),
            r"\1=[REDACTED]",
        ),
    ]

    # Combined pre-check regex: single search to detect if ANY sensitive data
    # might be present. >99% of log strings contain nothing sensitive, so this
    # avoids running 15 individual patterns on the common no-match path.
    # Uses re.IGNORECASE since this is a pre-check — false positives just trigger
    # the individual patterns which do their own precise matching.
    _ANY_SENSITIVE: re.Pattern[str] = re.compile(
        r"eyJ[A-Za-z0-9\-_]+\."  # JWT prefix
        r"|(?:api[_-]?key|secret|token|password|passwd|pwd|bearer|authorization)"  # key-value secrets
        r"|(?:challenge|credential_id|public_key|signature|attestation_object|"
        r"client_data_json|authenticator_data|user_handle)"  # passkey data
        r"|(?:postgresql|mysql|redis|mongodb)(?:\+\w+)?://"  # DB connection strings
        r"|AKIA[0-9A-Z]{16}"  # AWS access keys
        r"|[sprk]k_(?:live|test)_"  # Stripe keys
        r"|\b\d{3}[\s-]?\d{2}[\s-]?\d{4}\b"  # SSN
        r"|\b(?:\d{4}[\s-]?){3}\d{4}\b"  # Credit card
        r"|[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",  # Email
        re.IGNORECASE,
    )

    @classmethod
    def sanitize_message(cls, message: str) -> str:
        """Sanitize a log message by removing sensitive data."""
        if not isinstance(message, str):
            return message

        # Fast path: skip individual patterns if no sensitive data possible
        if not cls._ANY_SENSITIVE.search(message):
            return message

        sanitized = message
        for pattern, replacement in cls.SENSITIVE_PATTERNS:
            try:
                sanitized = pattern.sub(replacement, sanitized)
            except Exception as e:
                logging.warning("Error applying sanitization pattern: %s", e, exc_info=True)
                continue

        return sanitized

    @classmethod
    def sanitize_dict(cls, data: dict[str, Any], _depth: int = 0, _max_depth: int = 10) -> dict[str, Any]:
        """
        Recursively sanitize dictionary values.

        Args:
            data: Dictionary to sanitize
            _depth: Current recursion depth (internal)
            _max_depth: Maximum recursion depth to prevent stack overflow

        Returns:
            Sanitized dictionary with same structure
        """
        if _depth >= _max_depth:
            return {"_error": "[MAX_DEPTH_EXCEEDED]"}

        sanitized = {}
        for key, value in data.items():
            sanitized[key] = cls.sanitize_value(value, _depth, _max_depth)

        return sanitized

    @classmethod
    def sanitize_value(cls, value: Any, _depth: int = 0, _max_depth: int = 10) -> Any:
        """
        Sanitize a value based on its type.

        Args:
            value: Value to sanitize
            _depth: Current recursion depth (internal)
            _max_depth: Maximum recursion depth to prevent stack overflow

        Returns:
            Sanitized value
        """
        if _depth >= _max_depth:
            return "[MAX_DEPTH_EXCEEDED]"

        # Pass through primitives unchanged
        if value is None or isinstance(value, int | float | bool):
            return value

        # Sanitize strings
        if isinstance(value, str):
            return cls.sanitize_message(value)

        # Recursively sanitize dicts
        if isinstance(value, dict):
            return cls.sanitize_dict(value, _depth + 1, _max_depth)

        # Recursively sanitize lists
        if isinstance(value, list):
            return [cls.sanitize_value(item, _depth + 1, _max_depth) for item in value]

        # Recursively sanitize tuples
        if isinstance(value, tuple):
            return tuple(cls.sanitize_value(item, _depth + 1, _max_depth) for item in value)

        # Recursively sanitize sets and frozensets
        if isinstance(value, set | frozenset):
            return type(value)(cls.sanitize_value(item, _depth + 1, _max_depth) for item in value)

        # Sanitize bytes (e.g., WebAuthn passkey credential_id, challenge, public_key)
        if isinstance(value, bytes):
            try:
                decoded = value.decode("utf-8", errors="replace")
                sanitized_str = cls.sanitize_message(decoded)
                return sanitized_str.encode("utf-8")
            except Exception:
                return value

        # For other types, try to convert to string and sanitize
        try:
            return cls.sanitize_message(str(value))
        except Exception:
            return value


class SanitizingProcessor:
    """
    structlog processor for automatic log sanitization.

    Usage:
        Add to structlog processors list BEFORE the renderer:

        shared_processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            SanitizingProcessor(),  # <-- Add here
            structlog.processors.JSONRenderer(),
        ]
    """

    def __init__(self, max_depth: int = 10) -> None:
        """
        Initialize the sanitizing processor.

        Args:
            max_depth: Maximum recursion depth for nested dict sanitization.
                       Default 10 prevents stack overflow on circular refs.
        """
        self._max_depth = max_depth

    def __call__(self, logger: Any, method_name: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
        """Process log event by sanitizing all values in-place."""
        for key, value in list(event_dict.items()):
            event_dict[key] = LogSanitizer.sanitize_value(value, _max_depth=self._max_depth)
        return event_dict  # type: ignore[return-value]


class SecureFormatter(logging.Formatter):
    """
    Logging formatter that sanitizes sensitive data.

    Compatible with stdlib logging.

    Usage:
        handler = logging.StreamHandler()
        handler.setFormatter(SecureFormatter())
        logger.addHandler(handler)
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record with sanitization.

        Args:
            record: Log record to format

        Returns:
            Formatted and sanitized log message
        """
        # Sanitize the message
        original_msg = record.msg
        if isinstance(original_msg, str):
            record.msg = LogSanitizer.sanitize_message(original_msg)

        # Sanitize args if present
        if record.args:
            if isinstance(record.args, dict):
                record.args = LogSanitizer.sanitize_dict(record.args)
            elif isinstance(record.args, list | tuple):
                record.args = tuple(LogSanitizer.sanitize_value(arg) for arg in record.args)
            else:
                record.args = LogSanitizer.sanitize_value(record.args)


        # Format with parent formatter
        formatted = super().format(record)

        # Restore original message (in case record is reused)
        record.msg = original_msg

        return formatted


# Standalone helper functions for convenience


def sanitize_log_data(data: Any) -> Any:
    """
    Main entry point for sanitizing log data.

    Args:
        data: Data to sanitize (string, dict, list, etc.)

    Returns:
        Sanitized data
    """
    return LogSanitizer.sanitize_value(data)


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize a dictionary.

    Args:
        data: Dictionary to sanitize

    Returns:
        Sanitized dictionary
    """
    return LogSanitizer.sanitize_dict(data)


def sanitize_sensitive_data(data: Any) -> Any:
    """
    Alias for sanitize_log_data.

    Args:
        data: Data to sanitize

    Returns:
        Sanitized data
    """
    return sanitize_log_data(data)


def get_log_sanitizer() -> type[LogSanitizer]:
    """
    Get the LogSanitizer class.

    Returns:
        LogSanitizer class
    """
    return LogSanitizer


__all__ = [
    "LogSanitizer",
    "SanitizingProcessor",
    "SecureFormatter",
    "sanitize_log_data",
    "sanitize_dict",
    "sanitize_sensitive_data",
    "get_log_sanitizer",
]
