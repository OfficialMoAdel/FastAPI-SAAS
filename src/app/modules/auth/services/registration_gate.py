"""Shared S-M2 signup gate: closed-registration parity for /register and SSO.

While ``AUTH_OPEN_REGISTRATION`` is False (default), self-service
registration only bootstraps the FIRST user of a tenant. Joining an
existing tenant requires an admin-created account — without this, anyone
could join any tenant by guessing its ``X-Tenant-ID`` slug, and SSO
provides the same hole via IdP-validated email.

Callers raise their own layer-appropriate error (HTTP in routers, domain
errors in services); this helper only answers the boolean question so the
rule lives in exactly one place.
"""

import hmac

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.scoped import execute_scoped
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.modules.users.models import User

logger = get_logger(__name__)


async def is_self_registration_allowed(
    db: AsyncSession, *, registration_token: str | None = None
) -> bool:
    """Return True when a self-service signup may proceed.

    Mirrors the ``/account/register`` S-M2 gate exactly: open registration
    allows everything; otherwise an active tenant context that already has
    (non-deleted) users rejects new self-service signups. NO_TENANT
    (exempt paths) and inactive tenants fall through to the normal service
    path, where the duplicate/tenant checks are the honest failure.

    Run-3 finding ``tenancy.zero_user_tenant_claim_by_slug``:
    the empty-tenant case previously returned True unconditionally, which made
    the first-user bootstrap a standing, guessable target. It now requires the
    operator-issued ``FIRST_USER_REGISTRATION_TOKEN``.

    The comparison is constant-time and the token is compared in full; an
    unset token disables the bootstrap rather than permitting it.
    """
    if settings.auth.AUTH_OPEN_REGISTRATION:
        return True
    tenant = get_current_tenant()
    if tenant is None or not tenant.is_active:
        return True
    # P2-2: ownership check via the execute_scoped() seam (explicit-tenant
    # path — the helper holds the resolved tenant; an active context still
    # wins inside the seam), not a hand-rolled tenant_id comparison.
    stmt = select(User.id).where(User.is_deleted.is_(False)).limit(1)
    tenant_has_users = (
        await execute_scoped(
            stmt,
            db=db,
            model=User,
            scope=ExplicitTenant(tenant.id, reason="legacy-criterion:User"),
        )
    ).scalar() is not None
    if tenant_has_users:
        return False

    # Empty tenant: require proof the caller is the intended owner.
    expected = settings.auth.FIRST_USER_REGISTRATION_TOKEN
    if not expected:
        logger.warning(
            "first_user_bootstrap_disabled",
            reason="FIRST_USER_REGISTRATION_TOKEN is unset; empty tenants cannot be self-registered",
        )
        return False
    if not registration_token or not hmac.compare_digest(registration_token, expected):
        return False
    return True
