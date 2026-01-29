"""
Observability module for logging, tracing, and metrics.

This module provides:
- LogSanitizer: Removes sensitive data from log messages
- SanitizingProcessor: structlog processor for automatic sanitization
- SecureFormatter: stdlib logging formatter with sanitization
"""

from src.app.core.observability.log_sanitizer import (
    LogSanitizer,
    SanitizingProcessor,
    SecureFormatter,
    get_log_sanitizer,
    sanitize_dict,
    sanitize_log_data,
    sanitize_sensitive_data,
)

__all__ = [
    "LogSanitizer",
    "SanitizingProcessor",
    "SecureFormatter",
    "sanitize_log_data",
    "sanitize_dict",
    "sanitize_sensitive_data",
    "get_log_sanitizer",
]
