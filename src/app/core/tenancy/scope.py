"""Explicit scope for Tenant-scoped persistence (see CONTEXT.md).

This module owns the ONE tenant-scope decision. Both tenant-filter paths —
:meth:`TenantAwareCRUD.inject_tenant_filter` (CRUD kwargs path) and
:func:`core.db.scoped.criterion` (hand-built ``select()`` path) — delegate here, so the
context-wins / fail-closed / escape-hatch rule lives in exactly one place.

The underlying tenant resolution (contextvar + UUID canonicalization) stays
in :mod:`src.app.core.tenancy.query` / :mod:`src.app.core.tenancy.context`
(ADR-0006 — not re-litigated); this module owns the *decision* built on it.
"""

from dataclasses import dataclassfrom uuid import UUIDfrom src.app.core.tenancy.context import TenantContextErrorfrom src.app.core.tenancy.query import _resolve_tenant_id_from_context# Tenant ids flowing through the seam. Context ids are canonicalized to UUID
# by _resolve_tenant_id_from_context; caller-held ids pass through verbatim
# (some callers hold non-UUID values — canonicalizing here would crash them).
TenantIdValue = UUID | str | int


@dataclass(frozen=True)
class ExplicitTenant:
    """A caller-held tenant scope (worker paths, explicit-tenant services).

    Honored verbatim when no context is active. When a context IS active the
    context always wins and a mismatch is logged as a blocked override.
    ``reason`` is required so every explicit scoping is auditable.
    """

    tenant_id: TenantIdValue
    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("ExplicitTenant requires a non-empty reason")


@dataclass(frozen=True)
class Unscoped:
    """Explicit escape hatch for documented system contexts (no tenant filter).

    Only takes effect when no tenant context is active — with a context, the
    query stays scoped. Every use is audit-logged with ``reason``.
    """

    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("Unscoped requires a non-empty reason")


ExplicitScope = ExplicitTenant | Unscoped


def resolve_effective_tenant(
    scope: ExplicitScope | None, *, model_name: str
) -> TenantIdValue | None:
    """Resolve the effective tenant for ``model_name`` — the ONE scope decision.

    - Context active: it ALWAYS wins (mismatch with ``ExplicitTenant`` is
      logged as a blocked override; ``Unscoped`` is ignored — scoped, period).
    - No context + ``ExplicitTenant``: honored verbatim, caller takes
      responsibility (worker paths).
    - No context + ``Unscoped``: ``None`` (caller adds no filter), audit-logged.
    - No context + no scope: fail-closed — logs and raises ``TenantContextError``.

    Returns ``None`` only via the ``Unscoped`` escape hatch; never silently.
    """
    from src.app.core.logger import get_logger

    context_id = _resolve_tenant_id_from_context()
    if context_id is not None:
        if (
            isinstance(scope, ExplicitTenant)
            and str(scope.tenant_id) != str(context_id)
        ):
            get_logger(__name__).warning(
                "tenant_id_override_blocked",
                requested=str(scope.tenant_id),
                context=str(context_id),
            )
        return context_id
    if isinstance(scope, Unscoped):
        get_logger(__name__).warning(
            "tenant_context_missing_allowed_via_flag",
            model=model_name,
            reason=scope.reason,
        )
        return None
    if isinstance(scope, ExplicitTenant):
        return scope.tenant_id
    get_logger(__name__).error("tenant_context_missing_rejected", model=model_name)
    raise TenantContextError(
        f"Tenant-scoped query on {model_name} without active tenant context"
    )


__all__ = [
    "ExplicitScope",
    "ExplicitTenant",
    "TenantIdValue",
    "Unscoped",
    "resolve_effective_tenant",
]
