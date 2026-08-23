"""Redis cache utilities — tenant-scoped keys, stampede protection, invalidation.

This package decomposes the former single-module ``cache.py`` junk drawer into
one submodule per concern:

- :mod:`.keys` — cache-key construction (tenant scoping)
- :mod:`.tenant_index` — set-based tenant index for O(|S|) invalidation
- :mod:`.stampede` — XFetch probabilistic + lock-based stampede protection
- :mod:`.decorator` — the ``@cache`` endpoint decorator and its key helpers
- :mod:`.patterns` — O(1) pattern-based invalidation registry

Shared state & dependency seam
------------------------------
``pool`` / ``client`` (the Redis globals initialized at startup by
``core.setup``) and cross-cutting dependencies live in this package
namespace. Submodules resolve shared names through the package object
(``_state.<name>``), which preserves the exact late-binding lookup semantics
of the original single module — including runtime reassignment of
``cache.pool`` / ``cache.client`` and ``mock.patch("...utils.cache.<name>")``
test seams.

Rule: a name is accessed via ``_state`` iff it was a module-level name of the
original monolith referenced by more than one function or patched in tests;
pure-stdlib mechanics (``functools``, ``time``, ``re``, typing) and framework
types (``Request``/``Response``) are imported directly by submodules.
"""

import asyncio
import random
import re
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Response  # noqa: F401  (re-exported: historical public surface)
from redis.asyncio import ConnectionPool, Redis

from src.app.core.config import settings
from src.app.core.exceptions.cache_exceptions import (
    CacheIdentificationInferenceError,
    InvalidRequestError,
    MissingClientError,
)
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.monitoring.r1_unified_metrics import record_xfetch_latency
from src.app.core.utils.lua_manager import get_lua_manager

# Global set to hold strong references to background tasks (prevents GC of running tasks)
_background_tasks: set[Any] = set()

# Shared Redis connection pool / client; initialized by core.setup at startup.
pool: ConnectionPool | None = None
client: Redis | None = None

# U5: Pre-compiled regex pattern for cache decorator (performance optimization)
_BRACKET_PATTERN = re.compile(r"{(.*?)}")
MAX_SCAN_ITERATIONS = 1000

logger = get_logger(__name__)

# Structlog logger for stampede (name preserved from the monolith)
stampede_logger: Any = get_logger("src.app.core.utils.cache.stampede")

from uuid import UUID  # noqa: E402

# DI provider relocated to core/cache/dependencies.py; re-exported here
# for backward compatibility with existing import sites.
from src.app.core.cache.dependencies import async_get_redis  # noqa: E402,F401
from src.app.core.tenancy.context import get_current_tenant  # noqa: E402

from .decorator import (  # noqa: E402,F401
    _build_cache_key,
    _construct_data_dict,
    _delete_keys_by_pattern,
    _extract_data_inside_brackets,
    _format_extra_data,
    _format_prefix,
    _infer_resource_id,
    cache,
)
from .keys import build_tenant_key, tenant_cache_key, tenant_cache_pattern  # noqa: E402,F401
from .patterns import (  # noqa: E402,F401
    _pattern_index_key,
    invalidate_by_pattern,
    register_key_patterns,
    set_cache_with_patterns,
)
from .stampede import (  # noqa: E402,F401
    _refresh_cache,
    _refresh_cache_with_lock,
    get_with_lock,
    get_with_stampede_protection,
)
from .tenant_index import (  # noqa: E402,F401
    _tenant_index_key,
    _tenant_prefix_index_key,
    add_to_tenant_index,
    delete_tenant_cache,
    get_tenant_cache,
    get_tenant_index_size,
    invalidate_tenant_cache,
    migrate_tenant_cache_index,
    remove_from_tenant_index,
    set_tenant_cache,
)

