"""The audit DB-fallback must run inside an RLS bypass.

``audit_logs`` is RLS-enabled and FORCEd with
``WITH CHECK (tenant_id = auth.current_tenant_id())`` (migration
08_enable_rls_policies). ``_direct_insert_log`` stamps the payload's
tenant_id but opened a bare ``local_session()`` with no tenant bound, so
``auth.current_tenant_id()`` is NULL and the insert is rejected — the
fallback that exists to preserve the record would drop it.

Because the recorder applies ``BEST_EFFORT`` delivery, the loss was also
silent: nothing raised past ``audit()``, so ``audit_bypass_unaudited``
never fired.

The DB rejection itself is not asserted here (it needs a migrated
PostgreSQL with RLS FORCEd); what IS asserted is the property the fix
introduces — the fallback session carries the bypass, and a failure still
propagates so the loss stays loud.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.service import _direct_insert_log

TENANT = UUID("22222222-2222-2222-2222-222222222222")


def _payload() -> dict:
    return {
        "tenant_id": str(TENANT),
        "user_id": 7,
        "action": "user.updated",
        "entity_type": "user",
        "entity_id": "7",
        "details": {"changed_field": "name"},
        "ip_address": "203.0.113.10",
        "user_agent": "TestAgent/1.0",
        "created_at": datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC).isoformat(),
    }


def _session() -> MagicMock:
    db = MagicMock(spec=AsyncSession)
    db.connection = AsyncMock()
    db.commit = AsyncMock()
    return db


class TestFallbackRunsInsideBypassRls:
    @pytest.mark.asyncio
    async def test_fallback_session_is_bypass_bound(self) -> None:
        """Pre-fix the session was opened bare and the insert was rejected."""
        db = _session()

        with patch("src.app.core.db.database.local_session") as session_factory:
            session_factory.return_value.__aenter__.return_value = db
            await _direct_insert_log(_payload())

        db.info.__setitem__.assert_any_call("rls_bypass", True)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bypass_is_released_after_the_write(self) -> None:
        """The bypass must not outlive the session.

        bypass_rls deliberately leaves rls_bypass=True set (see the P2
        note in core/db/rls.py: the finally-clear keeps the flag so any
        bypass during teardown is still visible) and clears rls_user_id
        instead. Assert the real teardown, not a guess at it.
        """
        db = _session()

        with patch("src.app.core.db.database.local_session") as session_factory:
            session_factory.return_value.__aenter__.return_value = db
            await _direct_insert_log(_payload())

        assert db.info.__setitem__.call_args_list == [
            (("rls_bypass", True),),
            (("rls_user_id", None),),
        ], f"unexpected bypass lifecycle: {db.info.__setitem__.call_args_list}"

    @pytest.mark.asyncio
    async def test_row_still_carries_the_payload_tenant(self) -> None:
        """The bypass must not change what is written, only whether it lands."""
        db = _session()

        with patch("src.app.core.db.database.local_session") as session_factory:
            session_factory.return_value.__aenter__.return_value = db
            await _direct_insert_log(_payload())

        row = db.add.call_args.args[0]
        assert row.tenant_id == TENANT
        assert row.user_id == 7
        assert row.action == "user.updated"
        assert row.entity_type == "user"
        assert row.entity_id == "7"
        assert row.details == {"changed_field": "name"}
        assert row.ip_address == "203.0.113.10"
        assert row.user_agent == "TestAgent/1.0"

    @pytest.mark.asyncio
    async def test_write_failure_still_propagates(self) -> None:
        """A lost record must stay loud, not be swallowed by BEST_EFFORT.

        audit() applies the recorder's BEST_EFFORT *delivery* guarantee; if
        this path swallowed the failure the bypass would go unaudited with
        no audit_bypass_unaudited event at all.
        """
        db = _session()
        db.commit = AsyncMock(side_effect=RuntimeError("insert rejected"))

        with patch("src.app.core.db.database.local_session") as session_factory:
            session_factory.return_value.__aenter__.return_value = db
            with pytest.raises(RuntimeError, match="insert rejected"):
                await _direct_insert_log(_payload())
