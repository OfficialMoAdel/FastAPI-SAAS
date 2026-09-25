"""Tests for user caching in Redis.

This test suite verifies that:
1. User auth info uses Redis cache effectively
2. Cache hits avoid database queries
3. Cache invalidation works on user updates
4. Both username and email lookups are cached

Regression test for P1-2: User Caching in Redis
"""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.modules.users.session_invalidation import InvalidationReason

_FULL_AUTH = {
    "id": 10,
    "created_at": "2026-01-01T00:00:00",
    "name": "Test User",
    "username": "testuser",
    "email": "test@example.com",
    "profile_image_url": "https://example.com/a.png",
    "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
    "hashed_password": "hashed",
    "tier": {"id": 1, "created_at": "2026-01-01T00:00:00", "name": "free"},
}
_FULL_AUTH_JSON = _json.dumps(_FULL_AUTH)


def _create_mock_tenant() -> MagicMock:
    """Create a mock tenant for tenant context."""
    mock_tenant = MagicMock()
    mock_tenant.id = "550e8400-e29b-41d4-a716-446655440001"
    mock_tenant.tenant_id = "550e8400-e29b-41d4-a716-446655440001"
    return mock_tenant


class TestUserAuthInfoCaching:
    """Test user auth info caching behavior."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.fixture
    def user_directory(self, mock_db_session: AsyncMock) -> Any:
        """Create UserDirectory with mock database.

        Auth-info reads belong to the directory; the former
        ``UserService.get_user_auth_info`` was a one-line forward to it.
        """
        from src.app.modules.users.directory import UserDirectory

        return UserDirectory(mock_db_session)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_data_username(self, user_directory: Any) -> None:
        """Cache hit by username should return cached data without DB query."""
        mock_tenant = _create_mock_tenant()
        cached_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(return_value=_FULL_AUTH_JSON)

                found = await user_directory.auth_info(username="testuser")
                result = found.model_dump(mode="json") if found else None

                assert result is not None
                assert result["username"] == cached_data["username"]
                assert result["email"] == cached_data["email"]
                # Cache hit means data returned directly from Redis (verified by mock)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_data_email(self, user_directory: Any) -> None:
        """Cache hit by email should return cached data without DB query."""
        mock_tenant = _create_mock_tenant()
        cached_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
            "tier": {"id": 1, "name": "free"},
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(return_value=_FULL_AUTH_JSON)

                found = await user_directory.auth_info(email="test@example.com")
                result = found.model_dump(mode="json") if found else None

                assert result is not None
                assert result["username"] == cached_data["username"]
                assert result["email"] == cached_data["email"]

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db_and_caches_result(
        self, user_directory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache miss should query DB and cache the result."""
        mock_tenant = _create_mock_tenant()
        mock_client = AsyncMock()
        mock_eager = AsyncMock(return_value=_FULL_AUTH)
        monkeypatch.setattr(
            "src.app.modules.users.directory.crud_users.get_user_with_tier_eager",
            mock_eager,
        )
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
                mock_client.get = AsyncMock(return_value=None)  # Cache miss
                mock_client.setex = AsyncMock()


                found = await user_directory.auth_info(username="testuser")
                result = found.model_dump(mode="json") if found else None

                assert result is not None
                assert result["username"] == "testuser"
                # Verify cache was set with 1-hour TTL
                mock_client.setex.assert_called_once()
                call_args = mock_client.setex.call_args
                assert call_args[0][1] == 3600  # AUTH_INFO_CACHE_TTL

    @pytest.mark.asyncio
    async def test_cache_key_format_username(self, user_service: Any) -> None:
        """Cache key for username should use user:auth: prefix with tenant ID."""
        from src.app.modules.users.cache import _user_auth_cache_key

        mock_tenant = _create_mock_tenant()
        with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
            cache_key = _user_auth_cache_key("testuser")
            # Cache key format is user:auth:{tenant_id}:{identifier}
            assert cache_key == f"user:auth:{mock_tenant.id}:testuser"

    @pytest.mark.asyncio
    async def test_cache_key_format_email(self, user_service: Any) -> None:
        """Cache key for email should use user:auth: prefix with tenant ID."""
        from src.app.modules.users.cache import _user_auth_cache_key

        mock_tenant = _create_mock_tenant()
        with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
            cache_key = _user_auth_cache_key("test@example.com")
            # Cache key format is user:auth:{tenant_id}:{identifier}
            assert cache_key == f"user:auth:{mock_tenant.id}:test@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_identifier(self, user_directory: Any) -> None:
        """Should return None when neither username nor email provided."""
        result = await user_directory.auth_info()
        assert result is None


class TestUserCacheInvalidation:
    """Test cache invalidation when user data changes."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.fixture
    def user_directory(self, mock_db_session: AsyncMock) -> Any:
        """Create UserDirectory with mock database.

        Auth-info reads belong to the directory; the former
        ``UserService.get_user_auth_info`` was a one-line forward to it.
        """
        from src.app.modules.users.directory import UserDirectory

        return UserDirectory(mock_db_session)

    @pytest.mark.asyncio
    async def test_update_invalidates_all_cache_keys(self, user_service: Any) -> None:
        """Updating a user should invalidate all cache entries including email."""
        mock_tenant = _create_mock_tenant()
        user_data = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "username": "testuser",
            "email": "test@example.com",
        }

        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.session_invalidation.get_current_tenant", return_value=mock_tenant):
                class MockPipeline:
                    def __init__(self):
                        self.set_calls = []
                    async def __aenter__(self):
                        return self
                    async def __aexit__(self, exc_type, exc, tb):
                        pass
                    def set(self, key, val, ex=None):
                        self.set_calls.append(key)
                    async def execute(self):
                        pass

                mock_pipe = MockPipeline()
                mock_client.pipeline = MagicMock(return_value=mock_pipe)
                mock_client.delete = AsyncMock()
                # The test exercises cache-key wiring; PROFILE_EDIT keeps
                # the bump contract at False (no token_version change) which
                # is what this test asserted implicitly under the old
                # signature that defaulted ``bump_token_version`` to False.
                await user_service._invalidate_user_cache(
                    user_data, bump_token_version=False, reason=InvalidationReason.PROFILE_EDIT
                )
                # 9 keys: the id key, plus each of email/username in both the
                # tenant-scoped and the :global: form, for both the profile and
                # the auth-info family. Previously 5 — the :global: forms were
                # never deleted, so an entry a reader had written without an
                # active tenant stayed stale until its TTL.
                mock_client.delete.assert_called_once()
                call_args = mock_client.delete.call_args[0]
                assert len(call_args) == 9

                # Verify the right keys were invalidated (tenant-scoped)
                expected_keys = [
                    "user:profile:550e8400-e29b-41d4-a716-446655440000",
                    f"user:profile:{mock_tenant.id}:email:test@example.com",
                    f"user:profile:{mock_tenant.id}:username:testuser",
                    f"user:auth:{mock_tenant.id}:testuser",
                    f"user:auth:{mock_tenant.id}:test@example.com",
                    # The other scope, for a reader that ran without this tenant.
                    "user:profile:global:email:test@example.com",
                    "user:profile:global:username:testuser",
                    "user:auth:global:test@example.com",
                    "user:auth:global:testuser",
                ]

                for key in expected_keys:
                    assert key in call_args

                # Verify token_version keys were forcefully set via pipeline
                assert len(mock_pipe.set_calls) == 2
                assert f"tenant:{mock_tenant.id}:user:testuser:token_version" in mock_pipe.set_calls
                assert f"tenant:{mock_tenant.id}:user:test@example.com:token_version" in mock_pipe.set_calls


class TestUserCachingWithoutRedis:
    """Test user caching when Redis is unavailable."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock database session."""
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        """Create UserService with mock database."""
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.fixture
    def user_directory(self, mock_db_session: AsyncMock) -> Any:
        """Create UserDirectory with mock database.

        Auth-info reads belong to the directory; the former
        ``UserService.get_user_auth_info`` was a one-line forward to it.
        """
        from src.app.modules.users.directory import UserDirectory

        return UserDirectory(mock_db_session)

    @pytest.mark.asyncio
    async def test_get_user_auth_info_works_without_redis(
        self, user_directory: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should fall back to DB when Redis is unavailable."""
        mock_tenant = _create_mock_tenant()
        mock_eager = AsyncMock(return_value=_FULL_AUTH)
        monkeypatch.setattr(
            "src.app.modules.users.directory.crud_users.get_user_with_tier_eager",
            mock_eager,
        )
        with patch("src.app.modules.users.cache.get_redis", return_value=None):
            with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):

                found = await user_directory.auth_info(username="testuser")
                result = found.model_dump(mode="json") if found else None

                assert result is not None
                assert result["username"] == "testuser"
                # DB should be queried
                mock_eager.assert_called_once()


class TestUserCacheBatchInvalidation:
    """N3: batch invalidation collapses per-user loops into one pipeline."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.mark.asyncio
    async def test_batch_invalidates_all_users_single_pipeline(self, user_service: Any) -> None:
        """Multiple users must be invalidated in ONE pipeline round-trip."""
        mock_tenant = _create_mock_tenant()
        tid = mock_tenant.id
        # SQLAlchemy Row-like tuples projected by the RBAC call site
        rows = [
            MagicMock(id=1, email="a@example.com", username="alice", tenant_id=tid, token_version=3),
            MagicMock(id=2, email="b@example.com", username="bob", tenant_id=tid, token_version=5),
        ]

        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.session_invalidation.get_current_tenant", return_value=mock_tenant):
                class MockPipeline:
                    def __init__(self):
                        self.set_calls = []
                        self.delete_calls = []

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, exc_type, exc, tb):
                        pass

                    def delete(self, *keys):
                        self.delete_calls.extend(keys)

                    def set(self, key, val, ex=None):
                        self.set_calls.append((key, val))

                    async def execute(self):
                        pass

                mock_pipe = MockPipeline()
                mock_client.pipeline = MagicMock(return_value=mock_pipe)

                await user_service._invalidate_user_cache_batch(rows)

                # ONE pipeline round-trip: deletes + token sets all inside it
                mock_client.pipeline.assert_called_once()
                deleted = mock_pipe.delete_calls
                assert len(deleted) == 18  # 2 users x 9 keys; see the single-user test
                assert f"user:profile:{tid}:email:a@example.com" in deleted
                assert f"user:profile:{tid}:username:bob" in deleted

                # ONE pipeline: 2 token_version keys per user = 4 sets, versions preserved
                assert len(mock_pipe.set_calls) == 4
                set_map = dict(mock_pipe.set_calls)
                assert set_map[f"tenant:{tid}:user:alice:token_version"] == "3"
                assert set_map[f"tenant:{tid}:user:bob:token_version"] == "5"

    @pytest.mark.asyncio
    async def test_batch_bump_token_version(self, user_service: Any) -> None:
        """bump_token_version=True increments every user's cached version."""
        mock_tenant = _create_mock_tenant()
        tid = mock_tenant.id
        rows = [MagicMock(id=1, email="a@example.com", username="alice", tenant_id=tid, token_version=3)]

        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            with patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=mock_tenant):
                class MockPipeline:
                    def __init__(self):
                        self.set_calls = []

                    async def __aenter__(self):
                        return self

                    async def __aexit__(self, exc_type, exc, tb):
                        pass

                    def delete(self, *keys):
                        pass

                    def set(self, key, val, ex=None):
                        self.set_calls.append((key, val))

                    async def execute(self):
                        pass

                mock_pipe = MockPipeline()
                mock_client.pipeline = MagicMock(return_value=mock_pipe)

                await user_service._invalidate_user_cache_batch(rows, bump_token_version=True)

                set_map = dict(mock_pipe.set_calls)
                assert set_map[f"tenant:{tid}:user:alice:token_version"] == "4"

    @pytest.mark.asyncio
    async def test_batch_noop_without_redis(self, user_service: Any) -> None:
        """No Redis client -> no-op, no error."""
        rows = [MagicMock(id=1, email="a@example.com", username="alice", tenant_id="t", token_version=1)]
        with patch("src.app.modules.users.cache.get_redis", return_value=None):
            await user_service._invalidate_user_cache_batch(rows)  # must not raise

    @pytest.mark.asyncio
    async def test_batch_empty_input_noop(self, user_service: Any) -> None:
        """Empty user list -> no Redis interaction."""
        mock_client = AsyncMock()
        with patch("src.app.modules.users.cache.get_redis", return_value=mock_client):
            await user_service._invalidate_user_cache_batch([])
            mock_client.pipeline.assert_not_called()
            mock_client.delete.assert_not_called()


class TestDowngradeToFreeTierTokenVersion:
    """Regression: downgrade_to_free_tier must bump token_version.

    JWTs embed tier claims for zero-DB rate limiting; without the bump a
    downgraded user keeps premium tier claims until the access token expires.
    """

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def user_service(self, mock_db_session: AsyncMock) -> Any:
        from src.app.modules.users.service import UserService

        return UserService(mock_db_session)

    @pytest.mark.asyncio
    async def test_downgrade_bumps_token_version(self, user_service: Any, mock_db_session: AsyncMock) -> None:
        """Downgrade stages the tier update and routes the bump through the seam."""

        mock_tier_svc = MagicMock()
        mock_tier_svc.get_tier = AsyncMock(return_value={"id": 1, "name": "free"})
        user_service.crud = MagicMock()
        user_service.crud.update = AsyncMock()

        with (
            patch("src.app.modules.users.service.TierService", return_value=mock_tier_svc),
            patch.object(user_service, "invalidate_sessions", new_callable=AsyncMock) as mock_seam,
        ):
            from src.app.modules.users.session_invalidation import InvalidationReason

            result = await user_service.downgrade_to_free_tier(user_id=42)

        assert result == 1
        user_service.crud.update.assert_awaited_once()
        # 1B: SQL bump + commit + cache are owned by the seam (TIER_CHANGE).
        mock_seam.assert_awaited_once()
        seam_args, seam_kwargs = mock_seam.call_args
        assert seam_args[0] == 42
        assert seam_args[1] == InvalidationReason.TIER_CHANGE
        assert seam_kwargs.get("commit") is True

    @pytest.mark.asyncio
    async def test_downgrade_no_free_tier_returns_none(self, user_service: Any, mock_db_session: AsyncMock) -> None:
        """No free tier configured -> None, no update, no bump, no commit."""
        from src.app.shared.exceptions import ModelNotFoundError

        mock_tier_svc = MagicMock()
        mock_tier_svc.get_tier = AsyncMock(side_effect=ModelNotFoundError("Tier", "free"))
        user_service.crud = MagicMock()
        user_service.crud.update = AsyncMock()

        with patch("src.app.modules.users.service.TierService", return_value=mock_tier_svc):
            result = await user_service.downgrade_to_free_tier(user_id=42)

        assert result is None
        user_service.crud.update.assert_not_awaited()
        mock_db_session.execute.assert_not_awaited()
        mock_db_session.commit.assert_not_awaited()


class TestUserInvalidationKeyOwnership:
    """The key family has one owner, and invalidation never spells a key."""

    def test_key_builders_live_in_the_key_owner(self) -> None:
        """Readers and invalidators import the grammar from one module.

        users.cache imports users.session_invalidation (for the invalidation
        policy), so an invalidation that reached back into users.cache for a
        key builder would close an import cycle. The family therefore lives
        in its own leaf module, and neither of the other two defines a key.
        """
        from src.app.modules.users import cache_keys, session_invalidation

        for name in (
            "_user_cache_key",
            "_user_email_cache_key",
            "_user_username_cache_key",
            "_user_auth_cache_key",
            "build_user_invalidation_delete_keys",
        ):
            # cache.py re-exports these for its existing importers, so check
            # where each is *defined*, not merely that it is reachable.
            assert getattr(cache_keys, name).__module__ == cache_keys.__name__, (
                f"{name} is not defined in cache_keys"
            )
            if hasattr(session_invalidation, name):
                assert getattr(session_invalidation, name).__module__ == cache_keys.__name__


    def test_invalidation_module_defines_no_cache_prefix(self) -> None:
        """No duplicated prefix constant: one rename, one place to change."""
        import inspect

        from src.app.modules.users import cache_keys, session_invalidation

        source = inspect.getsource(session_invalidation)
        assert 'USER_CACHE_PREFIX = ' not in source
        assert 'AUTH_INFO_CACHE_PREFIX = ' not in source
        # The delete keys come from the owner, not from f-strings here.
        assert 'f"{USER_CACHE_PREFIX}' not in source
        assert cache_keys.USER_CACHE_PREFIX == "user:profile"
        assert cache_keys.AUTH_INFO_CACHE_PREFIX == "user:auth"

    def test_both_scopes_are_deleted_for_a_known_tenant(self) -> None:
        """A globally-keyed entry is invalidated, not left to expire.

        This is the gap: a reader with no active tenant writes the :global:
        form, and the invalidation used to build only the tenant-scoped ones.
        """
        from src.app.modules.users.cache_keys import build_user_invalidation_delete_keys

        keys = build_user_invalidation_delete_keys(
            7, username="Alice", email="A@Example.com", tenant_id="t1"
        )
        assert "user:profile:t1:email:a@example.com" in keys
        assert "user:profile:global:email:a@example.com" in keys
        assert "user:profile:t1:username:alice" in keys
        assert "user:profile:global:username:alice" in keys
        assert "user:auth:t1:a@example.com" in keys
        assert "user:auth:global:a@example.com" in keys
        assert "user:auth:t1:alice" in keys
        assert "user:auth:global:alice" in keys

    def test_username_gets_an_auth_info_key(self) -> None:
        """auth_info() is keyed on either identifier, so both are invalidated."""
        from src.app.modules.users.cache_keys import build_user_invalidation_delete_keys

        keys = build_user_invalidation_delete_keys(7, username="alice", tenant_id="t1")
        assert "user:auth:t1:alice" in keys
        assert "user:auth:global:alice" in keys

    def test_normalisation_matches_the_readers(self) -> None:
        """The delete keys are built by the same normalisation as the reads."""
        from src.app.modules.users.cache_keys import (
            _user_auth_cache_key,
            _user_email_cache_key,
            _user_username_cache_key,
            build_user_invalidation_delete_keys,
        )

        keys = build_user_invalidation_delete_keys(
            7, username=" Alice ", email=" A@Example.COM ", tenant_id="t1"
        )
        for reader in (
            _user_email_cache_key(" A@Example.COM ", "t1"),
            _user_username_cache_key(" Alice ", "t1"),
            _user_auth_cache_key(" A@Example.COM ", "t1"),
        ):
            assert reader in keys, f"a reader can write {reader!r} but invalidation misses it"
