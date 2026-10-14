"""Regression tests for `src/scripts/create_first_superuser.py`.

The module was completely unable to create a superuser, and the reason nothing
noticed is part of what is being pinned here.

Four independent defects, each one hidden behind the next:

1. `select(User)` raised `InvalidRequestError` because the script imported the
   model module but not `src.app.models` (the A-M6 registration seam), so
   `User`'s string-based relationships (`tenant`, `roles`, ...) were
   unresolvable at mapper-configure time.
2. The insert built `Table("user", ...)` — the model's `__tablename__` is
   `users`, and migration 01_foundation creates `users`. So the statement
   targeted a relation that does not exist.
3. That hand-built column list omitted `tenant_id`, `is_active` and
   `token_version`, all NOT NULL on the model, and its `profile_image_url`
   default had drifted from the model's.
4. `get_password_hash` is `async def` but was called without `await`, so the
   stored "hash" was a coroutine object.

And the reason all four survived: the whole body sat inside
`try/except Exception: logger.error(...)`, which logged and returned normally.
The script exited 0 while creating nothing, so no caller and no operator could
tell it had failed. That silent-failure shape is the primary thing this file
guards against now.

NOTE ON THE `real_postgres` FIXTURE (conftest.py:2124): it yields a plain
session with no rollback, so anything a test commits persists into the next
run. The first version of these tests therefore passed once and then silently
gated nothing — `create_first_user` found the account it had already made,
returned at its existence check, and never reached the insert. Every test here
purges its account first, which is what makes the assertions actually run.
"""

import pytest
from sqlalchemy import delete, select

from src.app.core.config import settings
from src.app.core.security import verify_password
from src.app.models import Tenant, User
from src.scripts.create_first_superuser import SYSTEM_TENANT_SLUG, create_first_user

pytestmark = pytest.mark.usefixtures("real_postgres_engine")


async def _ensure_tenant(session, slug: str) -> None:
    """Seed a tenant the way migration 01_foundation seeds the system one."""
    from tests.factories import TenantFactory

    if (await session.execute(select(Tenant.id).where(Tenant.slug == slug))).scalar_one_or_none() is not None:
        return
    await TenantFactory.create_async(session, slug=slug, name=slug)
    await session.commit()


async def _purge(session, *usernames: str) -> None:
    """Remove every account these tests create.

    Without this the second run short-circuits on `create_first_user`'s
    existence check and the insert-path assertions never execute.
    """
    for username in usernames:
        await session.execute(delete(User).where(User.username == username))
    await session.commit()


def _use_identity(monkeypatch, username: str, email: str) -> None:
    monkeypatch.setattr(
        "src.scripts.create_first_superuser.settings.first_user.ADMIN_USERNAME", username, raising=True
    )
    monkeypatch.setattr(
        "src.scripts.create_first_superuser.settings.first_user.ADMIN_EMAIL", email, raising=True
    )


class TestTheFirstSuperuserIsCreated:
    async def test_it_creates_a_superuser_that_can_actually_authenticate(self, real_postgres, monkeypatch):
        """The account is usable, not merely present.

        A bcrypt hash that verifies proves `get_password_hash` was awaited, and
        a live `is_active` proves the model's False default was overridden.
        """
        await _ensure_tenant(real_postgres, SYSTEM_TENANT_SLUG)
        await _purge(real_postgres, "s4_admin01")
        _use_identity(monkeypatch, "s4_admin01", "s4admin01@s4probe.org")

        await create_first_user(real_postgres)

        created = (
            (await real_postgres.execute(select(User).where(User.username == "s4_admin01"))).scalars().one()
        )
        assert created.tenant_id is not None
        assert created.is_active is True, "a first superuser that cannot log in is not created"
        assert created.is_email_verified is True
        assert created.is_superuser is True
        assert created.is_deleted is False
        # Coroutine objects are not valid hashes; this is how defect 4 surfaced.
        assert "coroutine" not in repr(created.hashed_password)
        assert created.hashed_password.startswith("$2")

        password = settings.first_user.ADMIN_PASSWORD
        assert password is not None
        assert await verify_password(password.get_secret_value(), created.hashed_password)

    async def test_running_it_twice_does_not_duplicate_or_raise(self, real_postgres, monkeypatch):
        await _ensure_tenant(real_postgres, SYSTEM_TENANT_SLUG)
        await _purge(real_postgres, "s4_admin02")
        _use_identity(monkeypatch, "s4_admin02", "s4admin02@s4probe.org")

        await create_first_user(real_postgres)
        await create_first_user(real_postgres)

        rows = (await real_postgres.execute(select(User).where(User.username == "s4_admin02"))).scalars().all()
        assert len(rows) == 1


class TestFailuresAreLoudInsteadOfSilent:
    async def test_a_missing_tenant_raises_rather_than_logging_and_exiting_zero(self, real_postgres):
        """The old `except Exception` is gone; the run must fail the process."""
        with pytest.raises(RuntimeError, match="No tenant with slug"):
            await create_first_user(real_postgres, tenant_slug="no-such-tenant-slug")

    async def test_an_unset_password_refuses_to_create_an_account(self, real_postgres, monkeypatch):
        await _ensure_tenant(real_postgres, SYSTEM_TENANT_SLUG)
        await _purge(real_postgres, "s4_admin03")
        _use_identity(monkeypatch, "s4_admin03", "s4admin03@s4probe.org")
        monkeypatch.setattr(
            "src.scripts.create_first_superuser.settings.first_user.ADMIN_PASSWORD", None, raising=True
        )

        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            await create_first_user(real_postgres)

        rows = (await real_postgres.execute(select(User).where(User.username == "s4_admin03"))).scalars().all()
        assert rows == []
