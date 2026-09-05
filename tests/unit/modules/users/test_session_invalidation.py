"""R2-02: session-invalidation policy tests.

Pins the security invariant: credential-affecting changes bump token_version
(force re-auth), benign profile edits do not. Also pins the agreement
contract in UserService._invalidate_user_cache: bump_token_version and
reason must agree so the two sources of truth cannot diverge.

These tests guard against the "silent no-op" regression where a caller
forgets to specify whether a change is credential-affecting and the cache
invalidation silently skips the token_version bump (leaving active sessions
of a freshly-reset-password user valid).
"""

import pytest

from src.app.modules.users.session_invalidation import (
    InvalidationReason,
    should_bump_token_version,
)


class TestShouldBumpTokenVersion:
    @pytest.mark.parametrize(
        "reason",
        [
            InvalidationReason.CREDENTIAL_CHANGE,
            InvalidationReason.DELETION,
            InvalidationReason.TIER_CHANGE,
        ],
    )
    def test_security_affecting_reasons_bump(self, reason):
        assert should_bump_token_version(reason) is True

    def test_profile_edit_does_not_bump(self):
        assert should_bump_token_version(InvalidationReason.PROFILE_EDIT) is False

    def test_every_reason_has_an_explicit_decision(self):
        """No reason may be silently unhandled — the policy must cover all."""
        for reason in InvalidationReason:
            assert isinstance(should_bump_token_version(reason), bool)


def _user(**overrides):
    base = {
        "id": 1,
        "email": "a@b.com",
        "username": "ab",
        "tenant_id": "t1",
        "token_version": 0,
    }
    base.update(overrides)
    return base


class TestInvalidateCacheReasonGuard:
    """``_invalidate_user_cache`` requires bump_token_version and reason to agree."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason",
        list(InvalidationReason),
    )
    async def test_agreement_with_policy_passes(self, reason):
        """reason + bump == should_bump_token_version(reason) is the happy path."""
        from unittest.mock import AsyncMock, patch

        from src.app.modules.users.service import UserService

        service = UserService(db=AsyncMock())
        mock_client = AsyncMock()
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            # must not raise
            await service._invalidate_user_cache(
                _user(),
                bump_token_version=should_bump_token_version(reason),
                reason=reason,
            )

    @pytest.mark.asyncio
    async def test_mismatched_bump_and_reason_raises(self):
        """A bump=True / PROFILE_EDIT pair is a programming error: must raise."""
        from unittest.mock import AsyncMock, patch

        from src.app.modules.users.service import UserService

        service = UserService(db=AsyncMock())
        with patch("src.app.modules.users.service.get_redis", return_value=AsyncMock()):
            with pytest.raises(ValueError, match="does not match"):
                await service._invalidate_user_cache(
                    _user(), bump_token_version=True, reason=InvalidationReason.PROFILE_EDIT
                )

    @pytest.mark.asyncio
    async def test_missing_arguments_is_type_error(self):
        """Both args are required (no defaults): caller must decide.

        A credential-affecting change that forgot to pass ``reason`` used to
        silently become a no-op for sessions. The new signature forces an
        explicit, type-checked decision at the call site.
        """
        from unittest.mock import AsyncMock, patch

        from src.app.modules.users.service import UserService

        service = UserService(db=AsyncMock())
        with patch("src.app.modules.users.service.get_redis", return_value=AsyncMock()):
            with pytest.raises(TypeError):
                await service._invalidate_user_cache(_user())  # type: ignore[call-arg]
            with pytest.raises(TypeError):
                await service._invalidate_user_cache(_user(), reason=InvalidationReason.CREDENTIAL_CHANGE)  # type: ignore[call-arg]
            with pytest.raises(TypeError):
                await service._invalidate_user_cache(_user(), bump_token_version=True)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reason,expected_bump",
        [
            (InvalidationReason.CREDENTIAL_CHANGE, True),
            (InvalidationReason.DELETION, True),
            (InvalidationReason.TIER_CHANGE, True),
            (InvalidationReason.PROFILE_EDIT, False),
        ],
    )
    async def test_credential_change_writes_bumped_token_version(self, reason, expected_bump):
        """Regression: a password reset (CREDENTIAL_CHANGE) MUST write current+1 to the
        token_version cache, not current. A PROFILE_EDIT MUST write current.

        Pins the (current_version, bump) -> new_version contract that the rate-limit
        middleware and auth deps rely on for token-version verification.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.app.modules.users.service import UserService

        written: dict[str, str] = {}

        # Sync pipe.set: capture (key, value) and return self
        mock_pipe = MagicMock()
        mock_pipe.set = MagicMock(side_effect=lambda k, v, ex=None: written.setdefault(k, v))
        # Async pipe.execute(): return any value, just needs to be awaitable
        mock_pipe.execute = AsyncMock(return_value=[])

        class _PipeCM:
            async def __aenter__(self_inner):
                return mock_pipe

            async def __aexit__(self_inner, *exc):
                return False

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=_PipeCM())
        mock_client.delete = AsyncMock()

        service = UserService(db=AsyncMock())
        with patch("src.app.modules.users.service.get_redis", return_value=mock_client):
            await service._invalidate_user_cache(
                _user(token_version=5),
                bump_token_version=should_bump_token_version(reason),
                reason=reason,
            )

        expected_version = 6 if expected_bump else 5
        assert written, "token_version cache keys were not written"
        for key, value in written.items():
            assert "token_version" in key, f"unexpected key written: {key}"
            assert value == str(expected_version), (
                f"reason={reason}: wrote token_version={value}, expected {expected_version}"
            )



class TestInvalidateUserSessions:
    """Unified invalidate_user_sessions seam tests."""

    @pytest.mark.asyncio
    async def test_invalidate_user_sessions_bumps_and_calls_cache(self):
        from unittest.mock import AsyncMock, patch

        from src.app.modules.users.session_invalidation import invalidate_user_sessions

        mock_db = AsyncMock()
        user = _user(id=42, token_version=2)

        patch_target = "src.app.modules.users.service.UserService._invalidate_user_cache"
        with patch(patch_target, new_callable=AsyncMock) as mock_inv:
            await invalidate_user_sessions(
                mock_db,
                user,
                reason=InvalidationReason.CREDENTIAL_CHANGE,
                commit=True,
            )

        # Verified: SQL update executed, committed, and cache invalidator called with bump=True
        mock_db.execute.assert_awaited_once()
        mock_db.commit.assert_awaited_once()
        mock_inv.assert_awaited_once_with(
            user,
            bump_token_version=True,
            reason=InvalidationReason.CREDENTIAL_CHANGE,
            explicit_token_version=None,
        )

    @pytest.mark.asyncio
    async def test_invalidate_user_sessions_profile_edit_no_bump(self):
        from unittest.mock import AsyncMock, patch

        from src.app.modules.users.session_invalidation import invalidate_user_sessions

        mock_db = AsyncMock()
        user = _user(id=42, token_version=2)

        patch_target = "src.app.modules.users.service.UserService._invalidate_user_cache"
        with patch(patch_target, new_callable=AsyncMock) as mock_inv:
            await invalidate_user_sessions(
                mock_db,
                user,
                reason=InvalidationReason.PROFILE_EDIT,
                commit=False,
            )

        # No SQL bump executed on benign profile edit
        mock_db.execute.assert_not_called()
        mock_db.commit.assert_not_called()
        mock_inv.assert_awaited_once_with(
            user,
            bump_token_version=False,
            reason=InvalidationReason.PROFILE_EDIT,
            explicit_token_version=None,
        )

    @pytest.mark.asyncio
    async def test_invalidate_user_sessions_raw_ids_projection(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.app.modules.users.session_invalidation import invalidate_user_sessions

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(id=1, email="a@b.com", username="a", tenant_id="t1", token_version=0),
            MagicMock(id=2, email="c@d.com", username="c", tenant_id="t1", token_version=0),
        ]
        mock_db.execute.side_effect = [mock_result, MagicMock()]

        patch_target = "src.app.modules.users.service.UserService._invalidate_user_cache_batch"
        with patch(patch_target, new_callable=AsyncMock) as mock_batch:
            await invalidate_user_sessions(
                mock_db,
                [1, 2],
                reason=InvalidationReason.CREDENTIAL_CHANGE,
                tenant_id="t1",
                commit=True,
            )

        # Projection select + sa_update were both executed
        assert mock_db.execute.await_count == 2
        mock_db.commit.assert_awaited_once()
        mock_batch.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
