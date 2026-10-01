"""Prove the RLS dirty-flag skip cannot leak one tenant's context to another.

The skip at `src/app/core/db/rls.py::restore_rls_context` short-circuits the
`set_config` round trip when `connection_record.info["rls_tenant"]` already
equals this checkout's tenant and `rls_dirty` is unset. That is a real
performance property (0 RTT instead of 1 on same-tenant reuse), and it is
also a security-relevant shortcut: it decides that a pooled connection
already asserts the right tenant's GUCs.

`TestRlsDirtyFlag` in `test_rls.py` pins each case of the decision matrix
against a fresh record. This module pins the property the matrix cannot see:
the state that survives a checkout is `connection_record.info`, so the same
record reused across a sequence of checkouts is exactly what a pool does.
Driven as a sequence rather than in isolation, a skip that leaks shows up.

No PostgreSQL required — the GUCs live in `connection_record.info`, so the
reuse is modelled directly rather than through a real server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.db.rls import bypass_rls, clear_rls_context, restore_rls_context, set_rls_context


class _PooledConnectionRecord:
    """Stands in for the pool's per-connection record.

    `info` is the dict that outlives a checkout, which is the whole reason the
    dirty-flag skip is possible and the whole reason it needs proving.
    """

    def __init__(self) -> None:
        self.info: dict[str, object] = {}


def _checkout(record: _PooledConnectionRecord | None, tenant_id: str | None):
    """Run one checkout and return the mocked connection it drove."""
    dbapi = MagicMock()
    tenant = None if tenant_id is None else MagicMock(id=tenant_id)
    with patch("src.app.core.db.rls.get_current_tenant", return_value=tenant):
        restore_rls_context(dbapi, record, None)
    inner = dbapi.run_async.call_args[0][0]
    conn = AsyncMock()
    return inner, conn


def _statements(inner, conn):
    """Drive the checkout coroutine and return the set_config calls it made."""
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(inner(conn))
    return [call.args for call in conn.execute.await_args_list]


def _tenant_asserted(statement_args, tenant_id: str) -> bool:
    """Did this statement assert `tenant_id` into the tenant GUC?"""
    if not statement_args:
        return False
    statement, *rest = statement_args
    return f"'{tenant_id}'" in statement or (rest and rest[0] == tenant_id)


class TestPooledReuseCannotLeakTenant:
    def test_repeated_same_tenant_checkouts_assert_once_then_skip(self) -> None:
        """T1, T1, T1 → one assertion, then nothing.

        This is the optimisation being pinned: the amortised case is the
        common one, because a pool hands the same connection back to the same
        tenant's requests most of the time.
        """
        record = _PooledConnectionRecord()

        inner1, conn1 = _checkout(record, "T1")
        assert len(_statements(inner1, conn1)) == 1

        for _ in range(2):
            inner, conn = _checkout(record, "T1")
            assert _statements(inner, conn) == [], (
                "same-tenant checkout on a clean connection must cost zero round trips"
            )

    def test_tenant_change_reasserts_on_every_crossing(self) -> None:
        """COUNTER-TEST: alternating tenants never skips.

        If the skip fired here, tenant A's `app.current_tenant` GUC would
        still be set on the connection when tenant B's request runs — a
        cross-tenant data leak, and the exact failure this optimisation could
        introduce.
        """
        record = _PooledConnectionRecord()

        # Establish the first tenant, then walk the chain in one direction so
        # each checkout really is a change.
        inner, conn = _checkout(record, "T1")
        assert len(_statements(inner, conn)) == 1
        assert record.info["rls_tenant"] == "T1"

        for left, right in [("T1", "T2"), ("T2", "T3"), ("T3", "T4"), ("T4", "T5")]:
            inner, conn = _checkout(record, left)
            # Same tenant as the last assert: this is the skip.
            assert _statements(inner, conn) == [], f"{left} repeated should have skipped"

            inner, conn = _checkout(record, right)
            statements = _statements(inner, conn)
            assert len(statements) == 1, f"tenant change {left}->{right} was skipped"
            assert _tenant_asserted(statements[0], right), (
                f"tenant change {left}->{right} did not assert the new tenant"
            )
            assert record.info["rls_tenant"] == right

    def test_returning_to_a_previously_seen_tenant_still_reasserts(self) -> None:
        """A → B → A must not take the skip on the way back.

        The record remembers only the most recent tenant, so returning to an
        older one is still a change and must be asserted.
        """
        record = _PooledConnectionRecord()

        for tenant in ("T1", "T2", "T1", "T2", "T1"):
            inner, conn = _checkout(record, tenant)
            assert len(_statements(inner, conn)) == 1, f"checkout for {tenant} was skipped"
            assert record.info["rls_tenant"] == tenant

    def test_out_of_listener_mutation_forces_reassertion(self) -> None:
        """A bypass between checkouts must defeat the skip.

        `bypass_rls` sets `app.bypass_rls` to true. Without the dirty flag the
        next same-tenant checkout would skip and hand the next request a
        connection that bypasses RLS entirely.
        """
        record = _PooledConnectionRecord()

        inner, conn = _checkout(record, "T1")
        assert len(_statements(inner, conn)) == 1

        # Simulate the mark-before-mutate that bypass_rls performs.
        record.info["rls_dirty"] = True

        inner, conn = _checkout(record, "T1")
        statements = _statements(inner, conn)
        assert len(statements) == 1, "a dirty connection was handed back without re-asserting"
        assert record.info.get("rls_dirty") is None, "dirty flag must clear after a successful re-assert"

    def test_failed_assertion_leaves_the_flag_set(self) -> None:
        """A failed `set_config` must not arm the skip.

        Fail-safe ordering: the flag is set before the mutation, so a failure
        leaves it set and the next checkout re-asserts rather than trusting a
        GUC that was never written.
        """
        record = _PooledConnectionRecord()
        record.info["rls_dirty"] = True
        record.info["rls_tenant"] = "T1"

        inner, conn = _checkout(record, "T1")
        conn.execute.side_effect = RuntimeError("connection lost")

        with pytest.raises(RuntimeError):
            import asyncio

            asyncio.new_event_loop().run_until_complete(inner(conn))

        assert record.info["rls_dirty"] is True, (
            "a failed re-assert cleared the dirty flag, so the next checkout would skip"
        )


class TestDirtyFlagIsSetBeforeMutation:
    @pytest.mark.asyncio
    async def test_set_rls_context_marks_before_writing(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncConnection

        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {}
        conn.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await set_rls_context(conn, "T1")

        assert conn.sync_connection.info == {"rls_dirty": True}

    @pytest.mark.asyncio
    async def test_clear_rls_context_marks_before_writing(self) -> None:
        from sqlalchemy.ext.asyncio import AsyncConnection

        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {}
        conn.execute.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await clear_rls_context(conn)

        assert conn.sync_connection.info == {"rls_dirty": True}

    @pytest.mark.asyncio
    async def test_bypass_leaves_the_flag_set_on_the_way_out(self) -> None:
        """A bypass pessimises the NEXT checkout, deliberately.

        `bypass_rls` marks dirty before the bypass SET and deliberately does
        not clear the flag on exit (see its comment at rls.py:388-390): any
        bypass during a checkout must force the next checkout to re-assert
        fail-closed, because the next request may inherit a connection with
        `app.bypass_rls` set to true. It costs one round trip and it is the
        correct trade.
        """
        from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

        conn = AsyncMock(spec=AsyncConnection)
        conn.sync_connection.info = {"rls_tenant": "T1"}
        session = AsyncMock(spec=AsyncSession)
        session.connection.return_value = conn

        async with bypass_rls(session, reason="test"):
            assert conn.sync_connection.info.get("rls_dirty") is True

        assert conn.sync_connection.info["rls_dirty"] is True, (
            "bypass_rls must leave the dirty flag set so the next checkout re-asserts"
        )
        assert conn.sync_connection.info["rls_tenant"] == "T1", (
            "bypass_rls must not clear the remembered tenant"
        )


class TestNoTenantCheckout:
    def test_no_tenant_never_skips_and_clears_the_remembered_tenant(self) -> None:
        """An unauthenticated checkout resets everything, unconditionally.

        A fresh pooled connection reads the GUC as NULL while an asserted-
        empty GUC reads as ''; the two differ per policy, so this branch is
        never allowed to take the skip.
        """
        record = _PooledConnectionRecord()

        inner, conn = _checkout(record, None)
        assert len(_statements(inner, conn)) == 1
        assert "rls_tenant" not in record.info

        # Second unauthenticated checkout: still asserts, because the record
        # now remembers nothing and a skip would be wrong.
        inner, conn = _checkout(record, None)
        assert len(_statements(inner, conn)) == 1
