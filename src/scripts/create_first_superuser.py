import asyncio
import logging
import uuid as uuid_pkg

from sqlalchemy import select

from src.app.core.config import settings
from src.app.core.db.database import AsyncSession, local_session
from src.app.core.logger import get_logger
from src.app.core.security import get_password_hash

# Importing the model registry (the A-M6 composition seam) is what configures the
# SQLAlchemy mapper: User declares its tenant/roles/... relationships by *string*
# name, so importing only the model module leaves them unresolvable and even
# `select(User)` raises InvalidRequestError.
from src.app.models import Tenant, User  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)

#: Migration 01_foundation seeds this tenant by construction — "Seed system tenant
#: ... for system-level audit logs and RLS bypass". `users.tenant_id` is NOT NULL
#: with a foreign key to it, so no superuser can be created without resolving it.
SYSTEM_TENANT_SLUG = "system"


async def _resolve_tenant_id(session: AsyncSession, tenant_slug: str) -> uuid_pkg.UUID:
    result = await session.execute(select(Tenant.id).where(Tenant.slug == tenant_slug))
    tenant_id = result.scalar_one_or_none()
    if tenant_id is None:
        raise RuntimeError(
            f"No tenant with slug {tenant_slug!r} exists. 01_foundation seeds {SYSTEM_TENANT_SLUG!r}, "
            "so run the migrations before creating the first superuser."
        )
    return tenant_id


async def create_first_user(session: AsyncSession, tenant_slug: str = SYSTEM_TENANT_SLUG) -> None:
    """Create the first superuser from ``settings.first_user``.

    The insert goes through the ORM model rather than a hand-built ``Table``
    mirror. That mirror is what made this script unable to work: it named the
    table ``user`` when the model's ``__tablename__`` is ``users``, and it omitted
    ``tenant_id``, ``is_active`` and ``token_version``, all NOT NULL. Every column
    default now lives on the model, so constructing a ``User`` cannot drift from
    the schema the way a duplicated column list does.

    Failures propagate. The previous ``except Exception`` logged and returned
    normally, so the script exited 0 while creating nothing — which is why the
    defects below survived: nothing downstream could tell it had failed.
    """
    first_user = settings.first_user
    name = first_user.ADMIN_NAME
    email = first_user.ADMIN_EMAIL
    username = first_user.ADMIN_USERNAME

    password = first_user.ADMIN_PASSWORD
    if password is None or not password.get_secret_value():
        raise RuntimeError(
            "first_user.ADMIN_PASSWORD is unset or empty; refusing to create a superuser with no password."
        )
    # get_password_hash is a coroutine; without the await this stored the coroutine
    # object itself as the hash.
    hashed_password = await get_password_hash(password.get_secret_value())

    tenant_id = await _resolve_tenant_id(session, tenant_slug)

    # Email and username are unique per tenant (uq_user_email_tenant /
    # uq_user_username_tenant, partial on is_deleted = false), so the existence
    # check has to be tenant-scoped — a global one would find an unrelated
    # tenant's admin and skip creating this tenant's.
    existing = await session.execute(
        select(User.id).where(
            User.tenant_id == tenant_id,
            User.email == email,
            User.is_deleted == False,  # noqa: E712
        )
    )
    if existing.scalar_one_or_none() is not None:
        logger.info("Admin user already exists.", username=username, tenant_id=str(tenant_id))
        return

    session.add(
        User(
            tenant_id=tenant_id,
            name=name,
            email=email,
            username=username,
            hashed_password=hashed_password,
            # The model defaults these to False, which would create an admin that
            # cannot log in. The first superuser is the one account that must be
            # able to.
            is_active=True,
            is_email_verified=True,
            is_superuser=True,
        )
    )
    await session.commit()
    logger.info("Admin user created successfully.", username=username, tenant_id=str(tenant_id))


async def main() -> None:
    async with local_session() as session:
        await create_first_user(session)


if __name__ == "__main__":
    asyncio.run(main())
