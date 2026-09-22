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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings
from src.app.core.db.scoped import criterion
from src.app.core.tenancy.context import get_current_tenant
from src.app.modules.users.models import User


async def is_self_registration_allowed(db: AsyncSession) -> bool:
    """Return True when a self-service signup may proceed.

    Mirrors the ``/account/register`` S-M2 gate exactly: open registration
    allows everything; otherwise an active tenant context that already has
    (non-deleted) users rejects new self-service signups. NO_TENANT
    (exempt paths) and inactive tenants fall through to the normal service
    path, where the duplicate/tenant checks are the honest failure.
    """
    if settings.auth.AUTH_OPEN_REGISTRATION:
        return True
    tenant = get_current_tenant()
    if tenant is None or not tenant.is_active:
        return True
    # P2-2: ownership criterion via the criterion() seam (explicit-tenant
    # path — the helper holds the resolved tenant; an active context still
    # wins inside criterion), not a hand-rolled tenant_id comparison.
    stmt = select(User.id).where(criterion(User, tenant.id), User.is_deleted.is_(False)).limit(1)
    tenant_has_users = (await db.scalar(stmt)) is not None
    return not tenant_has_users
