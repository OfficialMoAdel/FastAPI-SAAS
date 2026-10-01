"""Response schemas for the monitoring surface (``api/monitoring.py``).

The probe response shape is declared here rather than left as a bare
``dict[str, ...]`` annotation so the boundary between "safe to publish on
/api/health" and "operator-only" is reviewable in one place.

The split is the point of this module:

* :class:`ProbeCheck` — everything a health probe may publish. Its ``error``
  field carries the exception CLASS NAME only (``"OperationalError"``); raw
  driver text (DSN, credentials, the failed statement) is never modelled
  here, so it cannot be serialized onto a probe response even by accident.
* :class:`MetricsErrorResponse` — the superuser-gated ``/metrics`` failure
  envelope. It carries the full text in a SEPARATE, explicitly named
  ``error_detail`` field precisely because it must not be confusable with
  the probe shape: a client reading a probe response has no field to
  accidentally read the raw text off.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProbeCheck(BaseModel):
    """One component's probe result, as published on ``/api/health``.

    ``error`` is the exception CLASS NAME (``"OperationalError"``), never
    ``str(exc)``: SQLAlchemy/asyncpg/redis-py exception text routinely
    embeds the failed statement, the DSN, and connection credentials. The
    full text goes to the logs (keyed by ``request_id``) and, for a
    superuser, to ``/metrics``.

    Every field below is safe to publish to a credential-less caller that
    presented a tenant slug. Adding a raw-exception field here is the
    regression this module exists to make impossible.
    """

    status: str
    # Exception CLASS NAME only — never exception text.
    error: str | None = None
    # Static operator-facing copy (e.g. "Redis not configured"). Never dynamic.
    message: str | None = None
    latency_ms: float | None = None
    pool_size: int | None = None
    max_overflow: int | None = None
    queue_size: int | None = None
    workers: int | None = None
    max_jobs: int | None = None
    xfetch_hit_rate: float | None = None
    rbac_hit_rate: float | None = None
    dlq_size: int | float | None = None
    dlq_size_pct: int | float | None = None
    threshold_exceeded: bool | None = None


class HealthCheckResponse(BaseModel):
    """Health check response model."""

    status: str
    timestamp: str
    checks: dict[str, ProbeCheck]


class MetricsErrorResponse(BaseModel):
    """Failure envelope for the superuser-gated ``/metrics`` export.

    ``error_detail`` holds the FULL exception text: the route is gated on
    ``get_current_superuser`` and an operator scraping metrics is exactly
    the person who needs the DSN/driver message to diagnose. It is a
    distinct field name, absent from :class:`ProbeCheck`, so no probe
    response can grow it by reuse.
    """

    status: Literal["error"] = "error"
    # Exception CLASS NAME, matching the probe field's meaning.
    error_type: str
    # FULL exception text — operator-only surface.
    error_detail: str
    request_id: str | None = None
