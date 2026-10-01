"""The ONE superuser tenant rule — shared by both gates that enforce it.

P1: the project has a single documented rule and two gates that implemented it
differently, which is how drift happens.

THE RULE (``core.rbac.dependencies``): a superuser may bypass permission
checks WITHIN their own tenant, but may never act AS another tenant. The
assertion therefore runs BEFORE the superuser bypass, or it does not run at
all.

Why one helper rather than two assertions that happen to match:

* ``requires_permission`` pinned the tenant before the bypass. The sibling
  ``get_current_superuser`` checked only ``is_active``/``is_superuser`` and
  never touched the tenant — so the same router enforced two different rules
  on adjacent mutations.
* A duplicated comparison is a promise that someone will honour once and
  forget the other. A shared function is the rule, stated once.

Why it reads the tenant from the PRINCIPAL, not the request:

Re-resolving the request tenant here would introduce a second, divergent
resolution path — which is precisely the bug. The principal already carries
the tenant it was authenticated for; the request tenant is what the caller is
asking to act as, and comparing those two is the whole check. BOTH gates must
therefore hand this function the tenant off the same ``AuthContext``.

Why the raw principal value is inspected before normalization:

``request_tenant_id`` answers "unparseable" and "absent" alike, with ``None``.
That is the right primitive for a *request* tenant, where "no tenant to bind"
and "not a UUID" mean the same thing. For the PRINCIPAL they do not: a
principal that names a tenant we cannot resolve is not tenant-less, it is
unidentifiable, and treating it as tenant-less is a fail-open. So the call
site branches on the raw ``user_tenant_id`` first, and only a genuinely
ABSENT principal skips the check. ``AuthenticatedUser.tenant_id`` is typed
``UUID | str | int | None``, so a slug or a legacy int is representable.

When either side is not a UUID there is no normalized pair to compare, so
the rule falls back to the string comparison it used before normalization
existed (``str(user) != str(request)``): equal non-UUID tenants still pass,
and a non-UUID principal can no longer act as a different tenant.

Tenant-less surfaces are unaffected: when the request binds no tenant
(``None`` or the ``NO_TENANT`` sentinel on an exempt path such as
``/api/v1/tenants``), there is nothing to compare against and the gate
degrades to the superuser check it always was. Those routes are guarded by
``get_current_superuser`` plus the tenant-scoped CRUD write filter.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant

logger = get_logger(__name__)


def request_tenant_id(request_tenant: Any) -> UUID | None:
    """Normalize a tenant reference to its UUID, or None if there is none.

    Accepts every shape the two gates can hand over:

    * a ``UUID`` (the domain type, already normalized);
    * a ``str`` (a transport value: header, path param);
    * a ``TenantContext`` — what ``get_current_tenant()`` actually returns.
      Its ``tenant_id`` attribute is read, NOT ``str(obj)``: stringifying the
      dataclass yields its repr, which never parses as a UUID and would make
      every comparison fail.

    ``None`` for a tenant-less request, and for anything unparseable — both
    mean "no tenant to bind", never "match".
    """
    if request_tenant is None:
        return None
    if isinstance(request_tenant, UUID):
        return request_tenant
    # TenantContext (or any object carrying the domain attribute).
    inner = getattr(request_tenant, "tenant_id", None)
    if inner is not None and inner is not request_tenant:
        return request_tenant_id(inner) if not isinstance(inner, UUID) else inner
    try:
        return UUID(str(request_tenant))
    except (ValueError, AttributeError, TypeError):
        return None


def enforce_superuser_tenant_rule(
    *,
    user_tenant_id: Any,
    request_tenant: Any = None,
    user_id: Any = None,
    gate: str,
) -> None:
    """Assert a principal may act in the requested tenant. THE rule.

    Args:
        user_tenant_id: tenant the principal was authenticated for.
        request_tenant: tenant the caller is asking to act as. Defaults to the
            bound request tenant when omitted.
        user_id: for the log line.
        gate: which gate is calling, so a refusal names its own source.

    Raises:
        ForbiddenError: the principal is not bound to the tenant it is acting
            as — including the case where either side is a non-UUID tenant id
            that does not match. A superuser does not get a pass here — that
            is the entire point.
    """
    from src.app.shared.exceptions import ForbiddenError

    if request_tenant is None:
        request_tenant = get_current_tenant()

    if not request_tenant:
        # Tenant-less surface: an exempt path (docs, health, Stripe webhooks)
        # or the cross-tenant operator prefix. There is no tenant to compare
        # against, so the tenant-binding rule does not apply and the caller's
        # own gate (superuser flag, permission bitmask) decides.
        return

    if user_tenant_id is None:
        # Genuinely tenant-less principal: nothing to bind, so the tenant rule
        # does not apply and the caller's own gate decides. Checked on the RAW
        # value, before normalization — see the module docstring.
        return

    resolved = request_tenant_id(request_tenant)
    principal = request_tenant_id(user_tenant_id)

    if principal is None or resolved is None:
        # At least one side is not a UUID, so there is no normalized value to
        # compare and ``request_tenant_id`` cannot tell "unparseable" from
        # "absent". Fall back to the PRE-REFACTOR string comparison, which is
        # what a non-UUID tenant id has always been compared by: ``1 == 1`` is
        # allowed, ``"acme" != "<uuid>"`` is refused. Falling back to the early
        # return instead would let a principal carrying ``tenant_id="acme"``
        # act as ANY tenant — the fail-open this rule exists to close.
        #
        # ``get_current_superuser`` hands over a ``TenantContext`` (it reads
        # ``auth_context.tenant``), whose ``str()`` is a dataclass repr that
        # equals nothing; unwrap to the id it carries first, or the two gates
        # would disagree on a non-UUID tenant.
        raw_request = getattr(request_tenant, "tenant_id", request_tenant)
        if str(user_tenant_id) != str(raw_request):
            logger.warning(
                "superuser_tenant_rule_denied",
                gate=gate,
                user_id=user_id,
                principal_tenant=str(user_tenant_id),
                request_tenant=str(raw_request),
                reason="non_uuid_comparison",
            )
            raise ForbiddenError("User does not belong to the current tenant.")
        return

    if principal != resolved:
        logger.warning(
            "superuser_tenant_rule_denied",
            gate=gate,
            user_id=user_id,
            principal_tenant=str(principal),
            request_tenant=str(resolved),
            reason="tenant_mismatch",
        )
        raise ForbiddenError("User does not belong to the current tenant.")


__all__ = ["enforce_superuser_tenant_rule", "request_tenant_id"]
