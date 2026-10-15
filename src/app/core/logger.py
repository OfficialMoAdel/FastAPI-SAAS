"""Structured logging configuration using structlog.

Provides consistent, structured JSON logging across the application.
Supports both development (pretty-printed) and production (JSON) formats.
"""

import copy
import logging
import os
import queue
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from typing import cast

import structlog
from structlog.types import Processor

from src.app.core.config import EnvironmentOption, settings
from src.app.core.observability.log_sampling import make_sampling_processor
from src.app.core.observability.log_sanitizer import SanitizingProcessor

# Log directory comes from settings (P2-13: was raw os.getenv here).
# AppSettings.LOG_DIR defaults to repo-root/logs and honors the LOG_DIR env var.
LOG_DIR = settings.app.LOG_DIR
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE_PATH = os.path.join(LOG_DIR, "app.log")

# Determine if we're in production
IS_PRODUCTION = settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION


# K1: background listener thread target — restarted if setup_logging() is
# called again (e.g. in tests). Stopped via shutdown_logging() from the
# lifespan teardown so buffered records flush before process exit.
_log_listener: QueueListener | None = None


class _PassThroughQueueHandler(QueueHandler):
    """QueueHandler that preserves structlog event dicts.

    CPython's QueueHandler.prepare() formats the record with the queue
    handler's own (stdlib) formatter and writes the resulting string back
    into record.msg — which destroys the event dict that structlog's
    wrap_for_formatter stashed there. The listener-side ProcessorFormatter
    then sees the structlog markers with a str msg and crashes
    ('str' object has no attribute 'copy'). Passing records through
    unformatted keeps the dict intact for the listener thread to format.

    S-35 FIX: nulling `args` / `exc_info` WITHOUT rendering first drops them on
    the floor for FOREIGN (stdlib) records. structlog's
    `wrap_for_formatter` puts a DICT in `record.msg`, so those must stay
    untouched — but a plain `logging.getLogger(__name__).warning("x %s", y)`
    has a str msg with `args`, and `record.getMessage()` then returns the raw
    "%s" template. Nulling `exc_info` likewise discarded the traceback.
    Render str messages FIRST (which inlines both the %-args and the
    traceback), then clear the originals.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        record = copy.copy(record)
        # Only str messages need rendering; a dict is structlog's event and
        # must reach the listener intact.
        if isinstance(record.msg, str):
            record.msg = self.format(record)
        record.args = None
        record.exc_info = None
        record.stack_info = None
        return record


def get_log_level() -> int:
    """Get log level from settings (LOG_LEVEL env) or default."""
    level = settings.app.LOG_LEVEL.upper()
    return getattr(logging, level, logging.INFO)


class LevelAwareBoundLogger(structlog.stdlib.BoundLogger):
    """A structlog bound logger whose ``is_enabled_for`` DELEGATES.

    A-3. structlog ships two families of bound logger and neither one
    serves this repository:

    * ``structlog.make_filtering_bound_logger(min_level)`` freezes its
      minimum at configure time. ``is_enabled_for(level)`` becomes
      ``level >= min_level`` forever, so a logger explicitly configured to
      DEBUG stays silent, which contradicts how stdlib logging behaves.
    * ``structlog.stdlib.BoundLogger`` delegates everything to the wrapped
      stdlib logger but has NO ``is_enabled_for`` at all -- it exposes
      ``isEnabledFor``. MEASURED: swapping the wrapper for it makes
      ``test_is_enabled_for_debug_is_false`` fail with
      ``AttributeError: 'BoundLogger' object has no attribute
      'is_enabled_for'``, which would break every hot-path guard A-3
      exists to serve.

    So the predicate is added, and it delegates:

        logger.is_enabled_for(DEBUG)  ==  logger.isEnabledFor(DEBUG)

    Two consequences, both wanted. At the root level a guard short-circuits,
    so a suppressed ``logger.debug()`` costs the predicate and nothing else
    (measured 28.90 -> 0.40 us). And a logger that has been deliberately
    configured to DEBUG still emits, so the behaviour matches stdlib
    logging rather than diverging from it.
    """

    def is_enabled_for(self, level: int) -> bool:
        """Whether ``level`` would be emitted by the wrapped stdlib logger."""
        return self._logger.isEnabledFor(level)


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
    ]

    if IS_PRODUCTION:
        # Production: JSON output for log aggregation
        shared_processors.append(structlog.processors.format_exc_info)
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Development: Pretty colored console output
        shared_processors.append(structlog.dev.set_exc_info)
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # P10: INFO/DEBUG sampling runs BEFORE sanitization, on the structlog-native
    # chain only. structlog 25.x ProcessorFormatter.format does not catch
    # DropEvent from foreign_pre_chain, so stdlib records must never reach the
    # sampler — they keep full sanitization, no sampling.
    structlog_processors = shared_processors + [
        # S-05c: per-event overrides. These were added to `make_sampling_processor`
        # but never forwarded to `SamplingProcessor`, so the whole Mapping
        # machinery was unreachable and a deployment setting a per-event rate got
        # the global one instead. Fixed in log_sampling.make_sampling_processor;
        # tests/unit/core/test_log_sampling.py pins that the map arrives.
        make_sampling_processor(
            settings.performance.LOG_SAMPLING_RATE,
            rates=settings.performance.LOG_SAMPLING_RATES,
        ),
        SanitizingProcessor(),  # Sanitize sensitive data before rendering
    ]

    # Configure standard library logging
    log_level = get_log_level()

    # Configure structlog
    # A-3: the level-aware wrapper. Without it the bound logger's
    # `is_enabled_for` is structurally always True, so a hot-path
    # `logger.debug()` guard never short-circuits and the call costs the
    # wrapper plus every processor on the chain even when DEBUG is off
    # (measured 28.90 -> 0.40 us). See LevelAwareBoundLogger for why neither
    # stock wrapper serves and what this one delegates to.
    structlog.configure(
        processors=structlog_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=LevelAwareBoundLogger,
        cache_logger_on_first_use=True,
    )

    # Create formatter that uses structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors + [SanitizingProcessor()],
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
    # K1: request threads only enqueue records; the console + rotating-file
    # handlers run on a dedicated QueueListener thread, so disk/console I/O
    # never blocks the event loop or request handlers.
    global _log_listener
    if _log_listener is not None:
        _log_listener.stop()
        _log_listener = None
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    queue_handler = _PassThroughQueueHandler(log_queue)
    queue_handler.setLevel(log_level)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(queue_handler)
    _log_listener = QueueListener(log_queue, console_handler, file_handler)
    _log_listener.start()

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def shutdown_logging() -> None:
    """Stop the background log listener, flushing buffered records.

    Called from the lifespan teardown. Safe to call when logging was never
    set up or was already shut down.
    """
    global _log_listener
    if _log_listener is not None:
        _log_listener.stop()
        _log_listener = None


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns
    -------
        Configured structlog logger.

        Typed as ``FilteringBoundLogger``, which is what this module's
        ``structlog.configure()`` produces: it sets ``wrapper_class`` to the
        level-aware filtering bound logger, and every member
        of that family exposes ``is_enabled_for``. Before A-3 it set no
        ``wrapper_class`` and relied on structlog's default, which is
        ``BoundLoggerFilteringAtNotset`` — the always-True member, and a
        SIBLING of the configured one rather than its base: measured,
        ``issubclass(BoundLoggerFilteringAtInfo, BoundLoggerFilteringAtNotset)``
        is ``False``. Only ``logging.NOTSET`` selects it again. The previous
        annotation claimed ``stdlib.BoundLogger``, which has no such method —
        a cast that asserted something untrue, so a level-guard written to
        avoid a real cost looked like a type error and tempted a "fix" that
        deleted the guard. Callers get the level-check API the runtime has.

    Usage:
        logger = get_logger(__name__)
        logger.info("User logged in", user_id=123, ip="1.2.3.4")
    """
    return cast(structlog.typing.FilteringBoundLogger, structlog.get_logger(name))


# Initialize logging on module import
# setup_logging()  # REMOVED: Called explicitly in main.py lifespan to avoid side effects
