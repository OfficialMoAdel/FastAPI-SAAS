"""Passkey revocation on password change / reset (run-3 N-3).

A passkey could be enrolled with nothing but a stolen access token, and no
credential-change path removed it, so an attacker's credential outlived the
victim's password rotation. These tests pin the revocation behaviour and the
flag that gates it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRevokeUserPasskeys:
    @pytest.mark.asyncio
    async def test_deletes_every_passkey_for_the_user(self) -> None:
        from src.app.modules.auth.services.step_up import revoke_user_passkeys

        with patch("src.app.core.config.settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE", True):
            db = MagicMock()
            result = MagicMock()
            result.rowcount = 3
            db.execute = AsyncMock(return_value=result)
            db.flush = AsyncMock()

            removed = await revoke_user_passkeys(db, user_id=42)

        assert removed == 3
        assert db.flush.await_count == 1

    @pytest.mark.asyncio
    async def test_scoped_to_the_user(self) -> None:
        """The DELETE must be filtered by user_id, not wipe the table."""
        from src.app.modules.auth.services.step_up import revoke_user_passkeys

        with patch("src.app.core.config.settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE", True):
            db = MagicMock()
            result = MagicMock()
            result.rowcount = 1
            db.execute = AsyncMock(return_value=result)
            db.flush = AsyncMock()

            await revoke_user_passkeys(db, user_id=77)

        stmt = db.execute.await_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "user_id" in compiled
        assert "77" in compiled
        # A bare DELETE without a WHERE would be a catastrophic bug here.
        assert "WHERE" in compiled.upper()

    @pytest.mark.asyncio
    async def test_flag_disabled_is_a_no_op(self) -> None:
        from src.app.modules.auth.services.step_up import revoke_user_passkeys

        with patch("src.app.core.config.settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE", False):
            db = MagicMock()
            db.execute = AsyncMock()
            db.flush = AsyncMock()

            assert await revoke_user_passkeys(db, user_id=1) == 0
            db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_is_secure(self) -> None:
        """The flag must default to True so the fix is not opt-in."""
        from src.app.core.config.auth import AuthSettings

        assert AuthSettings().REVOKE_PASSKEYS_ON_PASSWORD_CHANGE is True

    @pytest.mark.asyncio
    async def test_db_failure_does_not_break_the_password_change(self) -> None:
        """A broken revocation must not roll back a legitimate password change."""
        from src.app.modules.auth.services.step_up import revoke_user_passkeys

        with patch("src.app.core.config.settings.auth.REVOKE_PASSKEYS_ON_PASSWORD_CHANGE", True):
            db = MagicMock()
            db.execute = AsyncMock(side_effect=RuntimeError("db down"))
            db.flush = AsyncMock()

            assert await revoke_user_passkeys(db, user_id=1) == 0


class TestPasswordChangeCallsRevocation:
    def test_change_password_invokes_revocation(self) -> None:
        import inspect

        from src.app.modules.auth.services.passwords import PasswordLifecycleService

        src = inspect.getsource(PasswordLifecycleService.change_password)
        assert "revoke_user_passkeys" in src

    def test_reset_password_invokes_revocation(self) -> None:
        import inspect

        from src.app.modules.auth.services.passwords import PasswordLifecycleService

        src = inspect.getsource(PasswordLifecycleService.reset_password)
        assert "revoke_user_passkeys" in src

    def test_revocation_precedes_the_commit(self) -> None:
        """Ordering matters: the delete must ride the same transaction as the update."""
        import inspect
        import re

        from src.app.modules.auth.services.passwords import PasswordLifecycleService

        # Match the actual CALLS, not prose: the explanatory comments mention
        # both function names and would otherwise satisfy the index() search.
        revoke_call = re.compile(r"revoke_user_passkeys\s*\(")
        commit_call = re.compile(r"invalidate_sessions\s*\(")

        for name in ("change_password", "reset_password"):
            src = inspect.getsource(getattr(PasswordLifecycleService, name))
            revoke_at = revoke_call.search(src)
            commit_at = commit_call.search(src)
            assert revoke_at and commit_at, f"{name}: expected both calls to be present"
            assert revoke_at.start() < commit_at.start(), (
                f"{name}: revocation must be flushed before invalidate_sessions(commit=True)"
            )
