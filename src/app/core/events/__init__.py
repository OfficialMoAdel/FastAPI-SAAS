"""Event bus for publish/subscribe pattern across modules.

Supports priority-based event handling:
- CRITICAL: Synchronous, fire-and-forget (audit logs)
- NORMAL/LOW: Asynchronous via ARQ queue (emails, webhooks)

This package decomposes the former single-module ``events.py`` by concern:

- :mod:`.priority` — ``EventPriority`` levels
- :mod:`.bus` — the ``EventBus`` class, singleton, and public registration/reset interface
- :mod:`.worker` — the ARQ worker entry point (``process_event_handler``)

Shared seam
-----------
Names that tests patch at runtime (``settings``, ``get_lua_manager``,
``arq_circuit``, ``logger``, ...) live in this package namespace; submodules
resolve them through the package object so
``mock.patch("src.app.core.events.<name>")`` keeps working exactly as it did
against the monolith.
"""

from arq.connections import ArqRedis
from arq.constants import default_queue_name, job_key_prefix
from arq.jobs import serialize_job
from arq.utils import timestamp_ms

from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.monitoring.r1_unified_metrics import record_event_idempotency
from src.app.core.observability.tracing import trace_span
from src.app.core.utils.circuit_breaker import arq_circuit
from src.app.core.utils.lua_manager import get_lua_manager

logger = get_logger(__name__)

# arq constants/helpers re-exported into the package namespace so EventBus's
# enqueue paths resolve them identically to the original monolith globals.

from .bus import EventBus, event_bus, reset_event_bus  # noqa: E402,F401
from .priority import EventPriority  # noqa: E402,F401
from .worker import process_event_handler  # noqa: E402,F401
