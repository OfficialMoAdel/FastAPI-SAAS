"""Unified dead-letter queue storage, codec, and policy.

The ARQ monitor and EventBus intentionally share one Redis key. This module
owns the envelope shape, source semantics, retention policy, and queue
operations so those callers do not each grow their own implementation.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.redis_provider import get_redis
from src.app.core.utils.lua_manager import get_lua_manager

logger = get_logger(__name__)
_UNSET = object()

DLQ_KEY = "arq:dlq"
LEGACY_DLQ_KEY = "event_bus:dlq"
DEFAULT_MAX_SIZE = 10_000
DEFAULT_ALERT_THRESHOLD_PCT = 80.0


def update_dlq_size(size: int, max_size: int | None = None) -> None:
    """Delegate metric updates through the monitoring module for patchability."""
    from src.app.core import monitoring

    if max_size is None:
        monitoring.update_dlq_size(size)
    else:
        monitoring.update_dlq_size(size, max_size)


class DeadLetterQueue:
    """Own the unified queue's storage, codec, and operational policy."""

    key = DLQ_KEY
    legacy_key = LEGACY_DLQ_KEY

    def __init__(self, redis_client: Any = _UNSET) -> None:
        self._redis_client = redis_client

    def _client(self) -> Any | None:
        return self._redis_client if self._redis_client is not _UNSET else get_redis()

    @staticmethod
    def _settings() -> tuple[bool, int, float]:
        from src.app.core import config as config_module

        settings = config_module.settings
        r1 = getattr(settings, "r1_unified", settings)
        bounded = bool(
            getattr(r1, "DLQ_USE_BOUNDED_SIZE", getattr(settings, "DLQ_USE_BOUNDED_SIZE", True))
        )
        max_size = int(getattr(r1, "DLQ_MAX_SIZE", getattr(settings, "DLQ_MAX_SIZE", DEFAULT_MAX_SIZE)))
        threshold = float(
            getattr(
                r1,
                "DLQ_ALERT_THRESHOLD_PCT",
                getattr(settings, "DLQ_ALERT_THRESHOLD_PCT", DEFAULT_ALERT_THRESHOLD_PCT),
            )
        )
        return bounded, max_size, threshold

    @staticmethod
    def encode(entry: dict[str, Any]) -> str:
        """Serialize one canonical dead-letter envelope."""
        return json_dumps(entry, as_str=True)

    @staticmethod
    def decode(raw: str | bytes, *, include_legacy: bool = False) -> dict[str, Any] | None:
        """Decode one envelope, normalizing missing source and timestamps."""
        raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        try:
            parsed = json_loads(raw_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            if not include_legacy:
                return None
            parts = raw_text.rsplit("|", 1)
            if len(parts) != 2:
                return None
            try:
                legacy_timestamp = float(parts[1])
                first_parts = parts[0].split("|", 2)
                if len(first_parts) < 3:
                    return None
                parsed = {
                    "job_id": first_parts[0],
                    "function": first_parts[1],
                    "error": first_parts[2],
                    "timestamp": legacy_timestamp,
                }
            except (TypeError, ValueError):
                return None
        if not isinstance(parsed, dict):
            return None

        parsed.setdefault("source", "arq")
        timestamp_value = parsed.get("timestamp")
        if isinstance(timestamp_value, int | float):
            parsed.setdefault("timestamp_iso", datetime.fromtimestamp(timestamp_value, tz=UTC).isoformat())
        return parsed

    def stage(self, pipeline: Any, entry: dict[str, Any]) -> None:
        """Stage a bounded LPUSH/LTRIM write on an existing Redis pipeline."""
        bounded, max_size, _ = self._settings()
        pipeline.lpush(self.key, self.encode(entry))
        if bounded:
            pipeline.ltrim(self.key, 0, max_size - 1)

    def arq_entry(self, job_id: str, function_name: str, error: str) -> dict[str, Any]:
        """Build the canonical envelope for an ARQ job failure."""
        return {
            "job_id": job_id,
            "function": function_name,
            "error": error,
            "timestamp": time.time(),
            "source": "arq",
        }

    def event_entry(
        self,
        *,
        event_name: str,
        handler_name: str,
        data: dict[str, Any],
        error: str,
        attempt_count: int,
    ) -> dict[str, Any]:
        """Build the canonical envelope for an EventBus handler failure."""
        return {
            "job_id": f"bus:{event_name}:{handler_name}:{time.time_ns()}",
            "function": event_name,
            "error": error,
            "timestamp": time.time(),
            "event_name": event_name,
            "handler": handler_name,
            "data": data,
            "payload": data,
            "attempt_count": attempt_count,
            "failed_at": datetime.now(UTC).isoformat(),
            "source": "event_bus",
        }

    async def add_arq(self, job_id: str, function_name: str, error: str) -> None:
        """Append an ARQ failure using the shared retention policy."""
        client = self._client()
        if client is None:
            logger.warning("Redis client not initialized for dead-letter queue")
            return

        bounded, max_size, threshold = self._settings()
        try:
            if bounded:
                async with client.pipeline(transaction=True) as pipeline:
                    self.stage(pipeline, self.arq_entry(job_id, function_name, error))
                    pipeline.llen(self.key)
                    results = await pipeline.execute()
                size = int(results[-1])
                update_dlq_size(size, max_size)
                self._warn_near_capacity(size, max_size, threshold)
            else:
                await client.lpush(self.key, self.encode(self.arq_entry(job_id, function_name, error)))
                size = int(await client.llen(self.key))
                update_dlq_size(size)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to add ARQ failure to dead-letter queue", job_id=job_id)
            raise

    async def add_event(
        self,
        *,
        event_name: str,
        handler_name: str,
        data: dict[str, Any],
        error: str,
        attempt_count: int,
    ) -> None:
        """Append an EventBus failure using the shared retention policy."""
        client = self._client()
        if client is None:
            logger.warning("Redis not configured for dead-letter queue", event_name=event_name)
            return

        bounded, max_size, threshold = self._settings()
        entry = self.event_entry(
            event_name=event_name,
            handler_name=handler_name,
            data=data,
            error=error,
            attempt_count=attempt_count,
        )
        try:
            if bounded:
                async with client.pipeline(transaction=True) as pipeline:
                    self.stage(pipeline, entry)
                    pipeline.llen(self.key)
                    results = await pipeline.execute()
                size = int(results[-1])
                update_dlq_size(size, max_size)
                self._warn_near_capacity(size, max_size, threshold)
            else:
                await client.lpush(self.key, self.encode(entry))
                size = int(await client.llen(self.key))
                update_dlq_size(size)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to add event failure to dead-letter queue", event_name=event_name)
            raise

        logger.warning(
            "Event moved to dead-letter queue",
            event_name=event_name,
            handler=handler_name,
            attempt_count=attempt_count,
            error=error,
        )

    @staticmethod
    def _update_size(size: int, max_size: int | None = None) -> None:
        from src.app.core import monitoring

        if max_size is None:
            monitoring.update_dlq_size(size)
        else:
            monitoring.update_dlq_size(size, max_size)

    @staticmethod
    def _warn_near_capacity(size: int, max_size: int, threshold_pct: float) -> None:
        threshold = max_size * threshold_pct / 100
        if size > threshold:
            logger.warning(
                "Dead-letter queue approaching capacity",
                dlq_size=size,
                max_size=max_size,
                usage_pct=round(size * 100 / max_size, 1),
            )

    async def read(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        source: str | None = None,
        include_invalid: bool = False,
        include_legacy: bool = False,
    ) -> list[dict[str, Any]]:
        """Read normalized entries, optionally retaining malformed legacy rows."""
        client = self._client()
        if client is None:
            return []

        raw_entries = await client.lrange(self.key, offset, offset + limit - 1)
        entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            parsed = self.decode(raw, include_legacy=include_legacy)
            if parsed is None:
                if include_invalid:
                    raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                    entries.append({"raw": raw_text, "error": "parse_error"})
                continue
            if source is None or parsed.get("source") == source:
                entries.append(parsed)
        return entries

    async def size(self) -> int:
        """Return the current queue length."""
        client = self._client()
        return int(await client.llen(self.key)) if client is not None else 0

    async def retry(self, source: str | None = None) -> dict[str, Any] | None:
        """Remove and return the oldest entry, optionally filtered by source."""
        client = self._client()
        if client is None:
            return None

        if source is None:
            raw = await client.rpop(self.key)
            return self.decode(raw) if raw else None

        raw = await get_lua_manager().execute_or_fallback(
            client,
            "dlq_pop_filtered",
            keys=[self.key],
            args=[source],
            fallback_fn=lambda: self._retry_scan(client, source),
        )
        return self.decode(raw) if raw else None

    async def _retry_scan(self, client: Any, source: str) -> str | None:
        raw_entries = await client.lrange(self.key, 0, -1)
        for raw in reversed(raw_entries):
            parsed = self.decode(raw)
            if parsed is not None and parsed.get("source") == source:
                await client.lrem(self.key, 1, raw)
                return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        return None

    async def clear(self, source: str | None = None) -> int:
        """Clear all entries or only entries matching one source."""
        client = self._client()
        if client is None:
            return 0

        if source is None:
            count = int(await client.llen(self.key))
            await client.delete(self.key)
            return count

        cleared = await get_lua_manager().execute_or_fallback(
            client,
            "dlq_clear_filtered",
            keys=[self.key],
            args=[source],
            fallback_fn=lambda: self._clear_scan(client, source),
        )
        return int(cleared or 0)

    async def _clear_scan(self, client: Any, source: str) -> int:
        raw_entries = await client.lrange(self.key, 0, -1)
        cleared = 0
        for raw in raw_entries:
            parsed = self.decode(raw)
            if parsed is not None and parsed.get("source") == source:
                await client.lrem(self.key, 1, raw)
                cleared += 1
        return cleared

    async def drain_legacy(self) -> int:
        """Move legacy EventBus entries into the unified queue with source tags."""
        client = self._client()
        if client is None:
            return 0

        moved = 0
        while True:
            raw = await client.rpop(self.legacy_key)
            if not raw:
                break
            try:
                parsed = json_loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                parsed.setdefault("source", "event_bus")
                normalized = self.encode(parsed)
            else:
                normalized = self.encode({"source": "event_bus", "payload": parsed})
            await client.lpush(self.key, normalized)
            moved += 1
        return moved


__all__ = [
    "DEFAULT_MAX_SIZE",
    "DLQ_KEY",
    "LEGACY_DLQ_KEY",
    "DeadLetterQueue",
]
