"""Tenant-context lifecycle seam — the ONE owner of context bind/clear.

Five writer sites in four packages used to re-derive the same mechanics:
setting/clearing the request contextvar (middleware), rebuilding a
context from the event wire dict (event worker), fabricating a minimal
job context (ARQ worker), and hand-rolled save/restore (SSO, AI
background tasks) — plus a separately hand-packed wire format on the
event bus. This module owns all of it:

- :func:`tenant_to_wire` / :func:`tenant_from_wire` — the event wire
  format (both ends; keys frozen in :data:`TENANT_WIRE_KEYS`).
- :func:`bind_tenant` / :func:`unbind_tenant` — set + warning-tolerant
  clear primitives for call sites whose control flow cannot use a
  ``with`` block (event worker handler span).
- :func:`bound_tenant` — set + clear-in-finally for request/job spans
  (middleware, AI background tasks).
- :func:`preserved_tenant` — snapshot + restore-in-finally for spans
  that temporarily override a caller's context (SSO, ARQ jobs).
- :func:`fabricate_job_tenant` — minimal worker context from a raw
  tenant id (ARQ jobs, which have no request to resolve from).

The raw :func:`set_current_tenant` / :func:`clear_tenant_context`
primitives stay in :mod:`src.app.core.tenancy.context`; this module is
the lifecycle policy over them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    get_current_tenant,
    set_current_tenant,
)

logger = logging.getLogger(__name__)

# Event wire format keys. Frozen so the pack side (event bus) and the
# parse side (event worker) can never drift apart again.
TENANT_WIRE_KEYS: tuple[str, str, str, str] = ("id", "slug", "name", "is_active")


class TenantWireError(ValueError):
    """Malformed tenant wire payload (missing/null keys, bad UUID)."""


def tenant_to_wire(tenant: TenantContext | None) -> dict[str, Any] | None:
    """Pack a tenant context into the event wire dict (or ``None``).

    Falsy inputs (``None`` and the ``NO_TENANT`` exempt sentinel) pack
    to ``None`` — worker/system flows publish tenant-less events, and
    the worker skips restoration for them.
    """
    if not tenant:
        return None
    return {
        "id": str(tenant.id),
        "slug": tenant.slug,
        "name": tenant.name,
        "is_active": tenant.is_active,
    }


def tenant_from_wire(data: dict[str, Any] | None) -> TenantContext | None:
    """Rebuild a tenant context from an event wire dict (or ``None``).

    Args:
        data: The ``tenant`` sub-dict of an event ``_event_meta``
            envelope, or ``None`` when the event carries no tenant.

    Raises:
        TenantWireError: A key is missing/null or the id is not a UUID.
            Callers that must not poison the worker (event worker)
            catch this and dead-letter the event.
    """
    if not data:
        return None
    try:
        missing = [key for key in TENANT_WIRE_KEYS if data.get(key) is None]
        if missing:
            raise TenantWireError(f"Missing or null wire keys {missing} in tenant_info: {data}")
        raw_id = data["id"]
        tenant_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        return TenantContext(
            tenant_id=tenant_id,
            tenant_slug=data["slug"],
            tenant_name=data["name"],
            is_active=data["is_active"],
        )
    except TenantWireError:
        raise
    except Exception as e:  # noqa: BLE001 - every parse failure is a wire error
        raise TenantWireError(f"Malformed tenant wire payload: {e}") from e


def bind_tenant(tenant: TenantContext | None) -> None:
    """Bind a tenant context for the current task (lifecycle verb).

    Thin verb over :func:`set_current_tenant` so lifecycle ownership is
    greppable at call sites that cannot use :func:`bound_tenant`.
    """
    set_current_tenant(tenant)


def unbind_tenant() -> None:
    """Clear the bound context, never raising.

    A failing clear is logged (the middleware's historical posture) —
    event-loop teardown paths must not crash the worker over cleanup.
    """
    try:
        clear_tenant_context()
    except Exception as e:  # noqa: BLE001 - cleanup is best-effort
        logger.warning("Failed to clear tenant context: %s", e)


@contextmanager
def bound_tenant(tenant: TenantContext | None) -> Iterator[TenantContext | None]:
    """Bind a tenant for a span, clearing it on exit (leak-proof).

    The request/job shape: middleware request handling, background
    tasks that must not leak their context into the next unit of work.
    """
    bind_tenant(tenant)
    try:
        yield tenant
    finally:
        unbind_tenant()


@contextmanager
def preserved_tenant() -> Iterator[TenantContext | None]:
    """Yield the current tenant, restoring it on exit (save/restore guard).

    This sets nothing: the body is expected to bind its own context
    (SSO callback, ARQ job setup) and this guard hands the previous
    value — including ``None`` — back afterwards.
    """
    previous = get_current_tenant()
    try:
        yield previous
    finally:
        set_current_tenant(previous)


def fabricate_job_tenant(tenant_id: int | str | UUID) -> TenantContext:
    """Build the minimal worker context for a raw tenant id.

    ARQ jobs arrive with an id, not a request — there is nothing to
    resolve, so the context is fabricated (slug/name are fixed worker
    markers, active by construction; the DB/RLS layer, not this seam,
    decides what the id may touch).
    """
    parsed_id = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    return TenantContext(
        tenant_id=parsed_id,
        tenant_slug="job_context",
        tenant_name="Job Context",
        is_active=True,
    )


__all__ = [
    "TENANT_WIRE_KEYS",
    "TenantWireError",
    "bind_tenant",
    "bound_tenant",
    "fabricate_job_tenant",
    "preserved_tenant",
    "tenant_from_wire",
    "tenant_to_wire",
    "unbind_tenant",
]
