"""Unit tests for P0-2 merged ban+blacklist+GCRA helper and P0-3 blacklist skip."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.core.security.auto_ban import BanInfo, check_ban_blacklist_and_rate_limit


def _base_kwargs(**overrides):
    kwargs = {
        "client_ip": "10.0.0.1",
        "token": "jwt-token",
        "jti": "jti-1",
        "user_id": "42",
        "tier_name": "premium",
        "tenant_id": uuid4(),
        "tier_id_raw": 7,
        "anonymous_ip": "10.0.0.1",
        "path": "/api/v1/x",
    }
    kwargs.update(overrides)
    return kwargs


def _patch_common(mock_lua_execute=None, resolve_side_effect=None, redis_available=True):
    """Patch seams for the merged helper; returns ctx dict of mocks."""
    mock_redis = AsyncMock()
    mock_lua = MagicMock()
    if mock_lua_execute is not None:
        mock_lua.execute = mock_lua_execute
    return {
        "redis": mock_redis,
        "lua": mock_lua,
        "patches": [
            patch(
                "src.app.core.security.token_blacklist_cache._get_available_redis_client_for_blacklist",
                return_value=(mock_redis if redis_available else None, False),
            ),
            patch(
                "src.app.core.security.rate_policy.resolve_rate_limits",
                new_callable=AsyncMock,
                side_effect=resolve_side_effect or [(200, 60), (100, 60)],
            ),
            patch("src.app.core.utils.lua_manager.get_lua_manager", return_value=mock_lua),
        ],
    }


class TestMergedBlacklistRateLimit:
    @pytest.mark.asyncio
    async def test_clean_token_uses_authed_candidate_single_rtt(self):
        mock_exec = AsyncMock(return_value=[0, 1, 199, 0, 0, "", 0])
        ctx = _patch_common(mock_lua_execute=mock_exec)
        for p in ctx["patches"]:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"]:
                p.stop()

        assert verdict is False
        assert (limit, period) == (200, 60)
        assert key == "rate_limit:user:42:/api/v1/x" or "user:42" in key
        assert ban_info.is_banned is False
        assert rl == (False, 199, 0)
        mock_exec.assert_awaited_once()
        # Single RTT: one script execution for blacklist + ban + GCRA.
        _, kwargs = mock_exec.call_args
        assert kwargs["keys"][1] == "token:blacklist"
        assert kwargs["keys"][2] == f"gcra:{key}"

    @pytest.mark.asyncio
    async def test_blacklisted_token_downgrades_to_anonymous_candidate(self):
        mock_exec = AsyncMock(return_value=[1, 1, 99, 0, 0, "", 0])
        ctx = _patch_common(mock_lua_execute=mock_exec)
        for p in ctx["patches"]:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"]:
                p.stop()

        assert verdict is True
        assert (limit, period) == (100, 60)
        assert key == "rate_limit:anonymous:ip:10.0.0.1:/api/v1/x"
        assert ban_info.is_banned is False
        assert rl == (False, 99, 0)

    @pytest.mark.asyncio
    async def test_blacklisted_premium_resolves_anonymous_limit_not_premium(self):
        """P1: the anonymous (downgrade) candidate must resolve with
        tier_id=None — a revoked premium token must not keep premium
        per-path policy via the numeric tier cache."""
        mock_exec = AsyncMock(return_value=[1, 1, 99, 0, 0, "", 0])
        ctx = _patch_common(mock_lua_execute=mock_exec)
        resolve_mock = ctx["patches"][1].start()
        for p in ctx["patches"][2:]:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"]:
                p.stop()

        assert verdict is True
        calls = resolve_mock.await_args_list
        # First call: authed candidate keeps the caller's tier_id.
        assert calls[0].kwargs["tier_id"] == 7
        # Second call: anonymous candidate clears the whole tier identity.
        assert calls[1].kwargs["tier_id"] is None

    @pytest.mark.asyncio
    async def test_banned_ip_returns_ban_info(self):
        mock_exec = AsyncMock(return_value=[0, 0, 0, 5000, 1, "spam", 300])
        ctx = _patch_common(mock_lua_execute=mock_exec)
        for p in ctx["patches"]:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"]:
                p.stop()

        assert verdict is False
        assert ban_info.is_banned is True
        assert ban_info.reason == "spam"
        assert ban_info.expires_at is not None
        # Banned + GCRA-consumed in the same script: limited with retry.
        assert rl[0] is True
        assert rl[2] == 5

    @pytest.mark.asyncio
    async def test_limit_zero_delegates_to_sequential_helpers(self):
        mock_exec = AsyncMock()
        ctx = _patch_common(
            mock_lua_execute=mock_exec, resolve_side_effect=[(0, 60), (100, 60)]
        )
        extra = [
            patch(
                "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.app.core.security.auto_ban.check_ban_and_rate_limit",
                new_callable=AsyncMock,
                return_value=(BanInfo(is_banned=False), (True, 0, 60)),
            ),
        ]
        for p in ctx["patches"] + extra:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"] + extra:
                p.stop()

        assert verdict is False
        assert (limit, period) == (0, 60)
        assert rl == (True, 0, 60)
        mock_exec.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_redis_mirrors_no_client_policies(self):
        # No-Redis path resolves only the anonymous candidate (one call).
        ctx = _patch_common(redis_available=False, resolve_side_effect=[(100, 60)])
        for p in ctx["patches"]:
            p.start()
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"]:
                p.stop()

        # fail_closed=False -> verdict None (unknown), anonymous candidate.
        assert verdict is None
        assert key == "rate_limit:anonymous:ip:10.0.0.1:/api/v1/x"
        assert (limit, period) == (100, 60)
        assert ban_info.is_banned is False

    @pytest.mark.asyncio
    async def test_script_error_falls_back_to_sequential(self):
        mock_exec = AsyncMock(side_effect=Exception("NOSCRIPT"))
        ctx = _patch_common(mock_lua_execute=mock_exec)
        extra = [
            patch(
                "src.app.core.security.token_blacklist_cache.is_token_blacklisted",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.app.core.security.auto_ban.get_ban_info",
                new_callable=AsyncMock,
                return_value=BanInfo(is_banned=False),
            ),
            patch("src.app.core.utils.rate_limit.rate_limiter"),
        ]
        rl_mock = None
        for p in ctx["patches"] + extra:
            started = p.start()
            if p is extra[-1]:
                rl_mock = started
                rl_mock.check_rate_limit_algorithm = AsyncMock(return_value=(False, 50, 0))
        try:
            verdict, limit, period, key, ban_info, rl = await check_ban_blacklist_and_rate_limit(
                **_base_kwargs()
            )
        finally:
            for p in ctx["patches"] + extra:
                p.stop()

        assert verdict is False
        assert (limit, period) == (200, 60)
        assert rl == (False, 50, 0)


class TestCompositePrefetchSkipBlacklist:
    @pytest.mark.asyncio
    async def test_check_blacklist_false_empties_blacklist_keys(self):
        from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch

        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        mock_lua.execute = AsyncMock(return_value=[0, -1, 1, b"15"])

        with patch(
            "src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua
        ):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="valid_token",
                jti="jti-123",
                user_id=42,
                session_id="sid-456",
                tenant_id=uuid4(),
                check_bitmask=True,
                check_blacklist=False,
                redis_client=mock_redis,
            )

        assert is_blacklisted is None
        assert session_valid is True
        assert bitmask == 15
        _, kwargs = mock_lua.execute.call_args
        assert kwargs["keys"][0] == ""
        assert kwargs["keys"][1] == ""
        assert kwargs["args"][0] == ""

    @pytest.mark.asyncio
    async def test_check_blacklist_true_keeps_blacklist_keys(self):
        from src.app.core.security.auth_prefetch import execute_composite_auth_prefetch

        mock_redis = AsyncMock()
        mock_lua = MagicMock()
        mock_lua.execute = AsyncMock(return_value=[0, 0, 1, b"15"])

        with patch(
            "src.app.core.security.auth_prefetch.get_lua_manager", return_value=mock_lua
        ):
            is_blacklisted, session_valid, bitmask = await execute_composite_auth_prefetch(
                token="valid_token",
                jti="jti-123",
                user_id=42,
                session_id="sid-456",
                tenant_id=uuid4(),
                check_bitmask=True,
                check_blacklist=True,
                redis_client=mock_redis,
            )

        assert is_blacklisted is False
        _, kwargs = mock_lua.execute.call_args
        assert kwargs["keys"][0] != ""
        assert kwargs["keys"][1] != ""


class TestMergedMiddlewarePath:
    def test_authed_request_served_by_merged_helper_single_script(self):
        """End-to-end through the middleware with FakeRedis as the client.

        Proves the authed branch calls the merged helper (not the legacy
        pair) and that the FakeRedis emulation of ban_blacklist_gcra_check
        consumes GCRA budget exactly once per request.
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.app.core.security import auto_ban as auto_ban_mod
        from src.app.middleware.rate_limit import RateLimitMiddleware
        from tests.conftest import FakeRedis

        fake = FakeRedis()
        claims = {"sub": "42", "jti": "j9"}
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/v1/x")
        async def x():
            return {"ok": True}

        orig = auto_ban_mod.check_ban_blacklist_and_rate_limit
        with (
            patch(
                "src.app.core.security.request_admission.settings.security.AUTO_BAN_ENABLED",
                True,
            ),
            patch(
                "src.app.core.security.request_admission.get_cached_jwt_payload",
                return_value=claims,
            ),
            patch(
                "src.app.core.security.rate_policy.get_tier_policy_limits",
                new_callable=AsyncMock,
                return_value=(None, None),
            ),
            patch(
                "src.app.core.security.token_blacklist_cache._get_available_redis_client_for_blacklist",
                return_value=(fake, False),
            ),
            patch(
                "src.app.core.security.request_admission.check_ban_blacklist_and_rate_limit",
                wraps=orig,
            ) as mock_merged,
            patch(
                "src.app.core.security.request_admission.check_ban_and_rate_limit",
                side_effect=AssertionError("legacy path must not run for authed requests"),
            ),
        ):
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer sometoken"}
                r1 = client.get("/api/v1/x", headers=headers)
                r2 = client.get("/api/v1/x", headers=headers)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mock_merged.await_count == 2
        limit = int(r1.headers["X-RateLimit-Limit"])
        assert int(r1.headers["X-RateLimit-Remaining"]) == limit - 1
        assert int(r2.headers["X-RateLimit-Remaining"]) == limit - 2
        # Authed candidate consumed; anonymous candidate untouched.
        assert any(k.startswith("gcra:rate_limit:user:42:") for k in fake.store)
        assert not any("rate_limit:anonymous" in k for k in fake.store)
