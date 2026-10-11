import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

import asyncio
import os
import uuid

from sqlalchemy import text

# Project Imports
from app.core.db.database import async_session_maker
from app.core.security import get_password_hash
from benchmarks.perf_accounts import PERF_TIER_ID, PERF_TIERS, perf_username

# Pro tier: rate_limit grants 1000/hr on /users/me. A NULL tier falls
# through to the anonymous budget (10/hr per IP), which a load test
# exhausts within seconds and trips the auto-ban.
PERF_TEST_TIER_ID = PERF_TIER_ID  # from src.benchmarks.perf_accounts

#: Permissions the perf accounts are granted. Without these the fixture accounts
#: carry NO role at all, so every permission-gated route answers 403 - which is an
#: authorisation check working correctly, not a broken endpoint, but it caps the
#: measurable endpoint matrix at the unauthenticated and self-service routes.
#:
#: Deliberately READ-only. Granting `users:manage` or `notifications:manage` would
#: let a load test mutate state, and a benchmark that writes is not measuring what
#: a production read path costs.
#:
#: `users:read` unlocks GET /users; `notifications:read` unlocks GET /notifications.
#: The self-service rate-limit route is `users:manage` and stays out of reach.
PERF_ROLE_NAME = "perf-fixture"
PERF_PERMISSION_SLUGS: tuple[str, ...] = (
    "users:read",
    "notifications:read",
)

TENANT_SLUG = "test-tenant-e2e"
LEGACY_EMAIL = "premium_e2e@example.com"
DEFAULT_PASSWORD = "Str1ngst!"

SELECT_USER_ID = text("SELECT id FROM users WHERE email = :email")

INSERT_USER = text("""
    INSERT INTO users (email, hashed_password, name, username, tenant_id,
                     is_superuser, is_email_verified, is_active, is_deleted,
                     tier_id, token_version, profile_image_url, uuid, created_at, updated_at)
    VALUES (:email, :hashed_password, :name, :username, :tenant_id,
            :is_superuser, :is_email_verified, :is_active, :is_deleted,
            :tier_id, :token_version, :profile_image_url, :uuid, NOW(), NOW())
""")  # noqa: E501


def _pool_emails() -> list[str]:
    """Accounts seeded for tests/performance/locustfile.py.

    Mirrors the locustfile's own resolution order so seeding and load shape
    always agree: PERF_TEST_EMAILS wins, otherwise generate
    ``perf_user_<i>@example.com`` up to PERF_TEST_USER_COUNT.
    """
    explicit = os.getenv("PERF_TEST_EMAILS", "").strip()
    if explicit:
        return [email.strip() for email in explicit.split(",") if email.strip()]

    try:
        count = int(os.getenv("PERF_TEST_USER_COUNT", "100"))
    except ValueError:
        count = 100
    return [f"perf_user_{i}@example.com" for i in range(max(count, 1))]


async def _ensure_tenant(session) -> object:
    """Create the test tenant and return its id."""
    result = await session.execute(
        text("""
            INSERT INTO tenants (name, slug, is_active, storage_usage_bytes, file_count, feature_flags, created_at, updated_at)
            VALUES (:name, :slug, :is_active, :storage_usage_bytes, :file_count, :feature_flags, NOW(), NOW())
            ON CONFLICT (slug) DO UPDATE SET is_active = TRUE
            RETURNING id
        """),  # noqa: E501
        {
            "name": "Test Tenant E2E",
            "slug": TENANT_SLUG,
            "is_active": True,
            "storage_usage_bytes": 0,
            "file_count": 0,
            "feature_flags": "{}",
        },
    )
    tenant_id = result.scalar()

    # Safety net: a separate SELECT inside the same transaction returns nothing
    # under asyncpg right after an ON CONFLICT upsert, leaving tenant_id None.
    if tenant_id is None:
        result = await session.execute(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": TENANT_SLUG},
        )
        tenant_id = result.scalar()

    if tenant_id is None:
        raise RuntimeError(f"Could not resolve tenant id for slug '{TENANT_SLUG}'")

    return tenant_id


async def _ensure_tiers(session) -> None:
    """Create the tier rows the accounts reference.

    ``create_tables()`` creates the ``tier`` TABLE but never a row in it, so
    seeding an account with ``tier_id`` fails its foreign key:

        ForeignKeyViolationError: insert or update on table "users"
        violates foreign key constraint "users_tier_id_fkey"

    and nothing in the message points at the missing tier - it reads as a broken
    seeder rather than a missing prerequisite. The Pro tier also matters for the
    measurement itself: a NULL tier falls through to the anonymous budget
    (10/hr per IP), which a load test exhausts in seconds, so accounts left
    un-tiered produce a run voided on 429s rather than one that measures.
    """
    await session.execute(
        text(
            "INSERT INTO tier (id, name, created_at) VALUES (:id, :name, NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        [{"id": tier_id, "name": tier_name} for tier_id, tier_name in PERF_TIERS],
    )


async def _ensure_perf_role(session, tenant_id: object) -> int:
    """Create the fixture role and grant it read permissions. Idempotent.

    Returns the role id. Every statement is ON CONFLICT DO NOTHING, so a re-run
    against an existing database changes nothing and a fresh one builds the same
    role - which matters because a fixture that behaves differently on the second
    run than the first is a fixture whose measurements cannot be compared.
    """
    await session.execute(
        text(
            "INSERT INTO roles (tenant_id, name, description, is_system) "
            "VALUES (:tenant_id, :name, :description, false) "
            "ON CONFLICT (tenant_id, name) DO NOTHING"
        ),
        {
            "tenant_id": tenant_id,
            "name": PERF_ROLE_NAME,
            "description": "Fixture role for performance measurement. Read-only by design.",
        },
    )
    role_row = await session.execute(
        text("SELECT id FROM roles WHERE tenant_id = :tenant_id AND name = :name"),
        {"tenant_id": tenant_id, "name": PERF_ROLE_NAME},
    )
    role_id = role_row.scalar_one()

    # Permission rows are global; seed only the slugs that are missing.
    await session.execute(
        text(
            "INSERT INTO permissions (slug, description) "
            "SELECT unnest(CAST(:slugs AS text[])), 'performance fixture' "
            "ON CONFLICT (slug) DO NOTHING"
        ),
        {"slugs": list(PERF_PERMISSION_SLUGS)},
    )
    await session.execute(
        text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT :role_id, p.id FROM permissions p "
            "WHERE p.slug = ANY(CAST(:slugs AS text[])) "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"role_id": role_id, "slugs": list(PERF_PERMISSION_SLUGS)},
    )
    await session.flush()
    return int(role_id)


async def _assign_role(session, user_id: object, tenant_id: object, role_id: int) -> None:
    """Attach the fixture role to one account, idempotently."""
    await session.execute(
        text(
            "INSERT INTO user_roles (user_id, tenant_id, role_id) "
            "VALUES (:user_id, :tenant_id, :role_id) "
            "ON CONFLICT DO NOTHING"
        ),
        {"user_id": user_id, "tenant_id": tenant_id, "role_id": role_id},
    )


async def _create_user_if_missing(
    session, tenant_id: object, email: str, hashed_password: str
) -> tuple[object, bool]:
    """Insert a test user unless it already exists.

    Returns ``(user_id, created)``. The id is returned for BOTH outcomes on
    purpose: a re-run against an existing database creates nothing, and if the role
    were only granted to freshly-created accounts then the fixture would depend on
    whether the database was empty - so the same measurement would describe
    different permissions on a first run and a re-run.
    """
    result = await session.execute(SELECT_USER_ID, {"email": email})
    existing = result.scalar()
    if existing:
        return existing, False

    await session.execute(
        INSERT_USER,
        {
            "email": email,
            "hashed_password": hashed_password,
            "name": f"Perf {email.split('@')[0]}",
            "username": perf_username(email),
            "tenant_id": tenant_id,
            "is_superuser": False,
            "is_email_verified": True,
            "is_active": True,
            "is_deleted": False,
            # Pro tier: rate_limit grants 1000/hr on /users/me.
            # A NULL tier falls through to the anonymous budget
            # (10/hr per IP), which a load test exhausts in seconds.
            "tier_id": PERF_TEST_TIER_ID,
            "token_version": 1,
            "profile_image_url": "https://profileimageurl.com",
            "uuid": str(uuid.uuid4()),
        },
    )
    inserted = await session.execute(SELECT_USER_ID, {"email": email})
    return inserted.scalar(), True


async def setup_test_data():
    """Create test tenant and the per-virtual-user account pool."""
    password = os.getenv("PERF_TEST_PASSWORD", DEFAULT_PASSWORD)
    pool = _pool_emails()

    # The legacy single account is kept so existing scripts/docs keep working.
    emails = [LEGACY_EMAIL] + [e for e in pool if e != LEGACY_EMAIL]

    async with async_session_maker() as session:
        try:
            print("Setting up test data for locust...")

            await _ensure_tiers(session)
            print(f"Tiers ready: {', '.join(f'{i}={n}' for i, n in PERF_TIERS)}")

            tenant_id = await _ensure_tenant(session)
            print(f"Test tenant ready: {TENANT_SLUG} (id={tenant_id})")

            # Hash once: every pool account shares the same password, and bcrypt
            # is deliberately slow (~100ms), so 100 accounts would otherwise
            # spend ~10s just hashing.
            hashed_password = await get_password_hash(password)
            print(f"Ensuring {len(emails)} test user(s)...")

            role_id = await _ensure_perf_role(session, tenant_id)
            print(
                f"Perf role ready: {PERF_ROLE_NAME} (id={role_id}) "
                f"granting {', '.join(PERF_PERMISSION_SLUGS)}"
            )

            created = 0
            for index, email in enumerate(emails, start=1):
                user_id, was_created = await _create_user_if_missing(
                    session, tenant_id, email, hashed_password
                )
                if was_created:
                    created += 1
                await _assign_role(session, user_id, tenant_id, role_id)
                if index % 25 == 0:
                    print(f"  ...{index}/{len(emails)} checked")

            await session.commit()
            print(f"Test data setup complete! ({created} created, {len(emails) - created} already present)")
            print(
                "Each load-test virtual user logs in with its own account, so no "
                "account exceeds auth.AUTH_MAX_SESSIONS concurrent sessions."
            )

        except Exception as e:
            print(f"Setup failed: {e}")
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(setup_test_data())
