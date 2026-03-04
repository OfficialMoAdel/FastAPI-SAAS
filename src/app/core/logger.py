"""Structured logging configuration using structlog.

Provides consistent, structured JSON logging across the application.
Supports both development (pretty-printed) and production (JSON) formats.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import cast

import structlog
from structlog.types import Processor

from src.app.core.config import EnvironmentOption, settings
from src.app.core.observability.log_sanitizer import SanitizingProcessor

# Log directory setup
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE_PATH = os.path.join(LOG_DIR, "app.log")

# Determine if we're in production
IS_PRODUCTION = settings.ENVIRONMENT == EnvironmentOption.PRODUCTION


def get_log_level() -> int:
    """Get log level from environment or default."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level, logging.INFO)


def setup_logging() -> None:
    """Configure structlog with appropriate processors for the environment."""
    # Shared processors for both stdlib and structlog
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        SanitizingProcessor(),  # Sanitize sensitive data before rendering
    ]

    if IS_PRODUCTION:
        # Production: JSON output for log aggregation
        shared_processors.append(structlog.processors.format_exc_info)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Development: Pretty colored console output
        shared_processors.append(structlog.dev.set_exc_info)
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    log_level = get_log_level()

    # Create formatter that uses structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns
    -------
        Configured structlog logger

    Usage:
        logger = get_logger(__name__)
        logger.info("User logged in", user_id=123, ip="1.2.3.4")
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


# Initialize logging on module import
# setup_logging()  # REMOVED: Called explicitly in main.py lifespan to avoid side effects


# Export for backward compatibility
logging = logging
