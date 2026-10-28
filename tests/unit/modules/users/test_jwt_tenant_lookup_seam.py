"""The JWT tenant-resolution read: unscoped by necessity, guarded by policy.

WHY THIS FILE EXISTS

``users._users_tenant_lookup`` is the BOOTSTRAP read on the token path: it
answers "which tenant does this principal belong to" before any tenant is
known. ``jwt_service._resolve_tenant_id_for_token`` reaches it only AFTER
``payload["tenant_id"]`` and ``get_current_tenant()`` both came back empty, so
it is *by construction* a context-free read.

That makes the obvious "unify it with ``UserDirectory``" refactor wrong, not
merely risky: ``crud_users.get`` resolves its scope through ``criterion``, which
is fail-closed without a tenant context. Delegating would raise on every call —
and because the raise is caught upstream, logout would report success while
leaving the token valid.

So this file pins the three things that must stay true:

1. The read works with NO tenant context (the only situation it runs in).
2. It refuses a soft-deleted principal (the real asymmetry it used to have
   with ``crud_users.get``).
3. It normalises case the same way ``users/crud.py`` does, so one identifier
   resolves identically whichever door reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import src.app.models  # noqa: F401  (register every mapper before compiling)
from src.app.core.tenancy.scope import Unscoped


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _fake_db(scalar_value=None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar.return_value = scalar_value
    db.execute = AsyncMock(return_value=result)
    return db


class TestTheReadIsUnscopedOnPurpose:
    @pytest.mark.asyncio
    async def test_it_resolves_without_a_tenant_context(self) -> None:
        """The regression this guards: a fail-closed read would raise here.

        No ``bind_tenant`` anywhere in this test — exactly the state the token
        path is in when it calls the lookup.
        """
        from src.app.modules.users import _users_tenant_lookup

        tenant = uuid4()
        db = _fake_db(tenant)

        resolved = await _users_tenant_lookup("someone@example.com", db)

        assert resolved == tenant
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_it_passes_the_documented_unscoped_escape_hatch(self) -> None:
        """The bypass must be the auditable one, not a bare execute.

        ``execute_scoped`` with ``Unscoped(reason=...)`` names the reason in the
        audit log and applies the shared DB circuit guard. A raw
        ``db.execute`` bypasses both.
        """
        from src.app.modules.users import _users_tenant_lookup

        db = _fake_db(uuid4())
        with patch(
            "src.app.core.db.scoped.execute_scoped", new_callable=AsyncMock
        ) as execute_scoped:
            execute_scoped.return_value = MagicMock(scalar=MagicMock(return_value=None))
            await _users_tenant_lookup("someone@example.com", db)

        scope = execute_scoped.await_args.kwargs["scope"]
        assert isinstance(scope, Unscoped)
        assert scope.reason == "jwt-tenant-resolution"


class TestTheReadEnforcesTheSameGuardsAsTheCanonicalRead:
    @pytest.mark.asyncio
    async def test_a_soft_deleted_principal_resolves_no_tenant(self) -> None:
        """The security half of the card.

        A soft-deleted user must not resolve a tenant. Before this, the
        hand-built statement skipped ``is_deleted`` while ``crud_users.get``
        applied it, so the same identifier answered differently depending on
        which door read it.
        """
        from src.app.modules.users import _users_tenant_lookup

        db = _fake_db(None)
        with patch("src.app.core.db.scoped.execute_scoped", new_callable=AsyncMock) as execute_scoped:
            execute_scoped.return_value = MagicMock(scalar=MagicMock(return_value=None))
            await _users_tenant_lookup("someone@example.com", db)

        sql = _compiled(execute_scoped.await_args.args[0])
        assert "users.is_deleted IS false" in sql, (
            f"a soft-deleted principal must not resolve a tenant: {sql}"
        )

    @pytest.mark.asyncio
    async def test_both_identifier_arms_are_case_insensitive(self) -> None:
        """``users/crud.py`` lowercases; this read must agree.

        Compares the compiled predicate shape rather than a live row, because
        the two doors must agree on the SQL, not on a fixture.
        """
        from src.app.modules.users import _users_tenant_lookup

        db = _fake_db(uuid4())
        with patch("src.app.core.db.scoped.execute_scoped", new_callable=AsyncMock) as execute_scoped:
            execute_scoped.return_value = MagicMock(scalar=MagicMock(return_value=None))
            await _users_tenant_lookup("Someone@Example.COM", db)

        sql = _compiled(execute_scoped.await_args.args[0])
        assert "lower(users.username) = 'someone@example.com'" in sql
        assert "lower(users.email) = 'someone@example.com'" in sql

    @pytest.mark.asyncio
    async def test_it_selects_only_the_tenant_column(self) -> None:
        """Keep the cheap single-column read — this runs on every token check."""
        from src.app.modules.users import _users_tenant_lookup

        db = _fake_db(uuid4())
        with patch("src.app.core.db.scoped.execute_scoped", new_callable=AsyncMock) as execute_scoped:
            execute_scoped.return_value = MagicMock(scalar=MagicMock(return_value=None))
            await _users_tenant_lookup("someone@example.com", db)

        stmt = execute_scoped.await_args.args[0]
        assert len(stmt.selected_columns) == 1


class TestAFailedResolutionStillRevokes:
    """The blast radius that made the naive refactor dangerous.

    ``blacklist_tokens`` resolved the tenant INSIDE a try whose ``except`` only
    logged. A raise therefore skipped the fail-closed Redis write below it, and
    the token was revoked nowhere — logout would report success while the token
    stayed valid.
    """

    @pytest.mark.asyncio
    async def test_resolution_failure_falls_through_to_the_redis_fallback(self) -> None:
        from src.app.core.security import jwt_service

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.flush = AsyncMock()

        exp = datetime.now(UTC) + timedelta(hours=1)
        # Payloads are supplied so the decode step is skipped; the subject of
        # this test is the resolution failure, not signature verification.
        payload = {"exp": int(exp.timestamp()), "jti": "jti-1", "sub": "someone"}

        written: list[list[tuple[str, datetime]]] = []

        async def _fake_redis_write(entries):
            written.append(list(entries))

        with (
            patch.object(
                jwt_service,
                "_resolve_tenant_id_for_token",
                AsyncMock(side_effect=RuntimeError("tenant lookup exploded")),
            ),
            patch.object(jwt_service, "add_tokens_to_blacklist", AsyncMock(side_effect=_fake_redis_write)),
        ):
            await jwt_service.blacklist_tokens(
                access_token="access",
                refresh_token=None,
                db=db,
                access_payload=payload,
            )

        assert written, (
            "a failed tenant resolution skipped the Redis fallback, so the token "
            "was blacklisted nowhere — logout would report success"
        )
        members = [member for entries in written for member, _ in entries]
        assert "jti:jti-1" in members, f"the revoked member must be the jti key: {members}"
