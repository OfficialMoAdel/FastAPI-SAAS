"""Phase 3: one round-trip per anonymous request, and Redis's clock.

WHAT THIS PINS
    F-10  the anonymous per-IP aggregate ceiling is folded into the SAME
          composite EVALSHA as the per-path bucket, so anonymous traffic makes
          ONE Redis round-trip instead of two -- and the ADR-0015 invariant
          survives the fold: the aggregate is NOT consumed when the per-path
          leg rejects, and not at all when the ceiling is disabled.
    F-11  the GCRA clock comes from Redis (`redis.call('TIME')`) instead of
          being stamped in the application process, in all three variants.
    F-12  `get_violation_stats` reads each SCAN page with ONE `mget`.

WHY THESE ARE NOT BENCHMARKS
    Each assertion is a property of the call sequence (how many scripts ran,
    with which keys/args, and what the stored TAT is), not a timing. The Lua
    behaviour itself is exercised through the repo's FakeRedis emulation, which
    mirrors the script -- the arithmetic is the script's, so a divergence
    between the two would show up here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.security.auto_ban import check_ban_and_rate_limit, get_violation_stats
from src.app.core.security.request_admission import RequestAdmissionService
from src.app.core.utils.lua_manager import LuaScriptManager


class TestF11RedisClock:
    """The GCRA scripts take their clock from Redis, not from ARGV."""

    @pytest.mark.parametrize("script", ["rate_limit_gcra", "ban_and_gcra_check", "ban_blacklist_gcra_check"])
    def test_the_script_reads_redis_time(self, script: str) -> None:
        source = LuaScriptManager.BUILTIN_SCRIPTS[script]
        assert "redis.call('TIME')" in source, f"{script} must read Redis's clock"
        assert "local now_ms = tonumber(ARGV" not in source, f"{script} still takes now_ms from ARGV"

    def test_no_variant_reads_a_timestamp_argument(self) -> None:
        for script in ("rate_limit_gcra", "ban_and_gcra_check", "ban_blacklist_gcra_check"):
            source = LuaScriptManager.BUILTIN_SCRIPTS[script]
            assert "now_ms" in source, f"{script} must still use a now_ms local"
            # ...but always the one derived from TIME, never an ARGV read.
            assert source.count("local now_ms =") == 1


class TestF10AggregateFold:
    """The aggregate ceiling rides in the per-path composite."""

    @pytest.fixture
    def fake_redis(self) -> Any:
        from tests.conftest import FakeRedis

        return FakeRedis()

    @pytest.mark.asyncio
    async def test_one_script_call_consumes_both_buckets(self, fake_redis: Any) -> None:
        with patch("src.app.core.security.auto_ban.get_redis", return_value=fake_redis):
            _ban, _rl, aggregate = await check_ban_and_rate_limit(
                "1.2.3.4",
                "rate_limit:anonymous:ip:1.2.3.4:users_me",
                limit=10,
                period=60,
                aggregate_limit=3,
                aggregate_period=60,
            )

        assert aggregate is not None
        assert aggregate[0] is False, "the first request is under both ceilings"
        # BOTH buckets advanced inside the ONE call.
        assert fake_redis.store["gcra:rate_limit:anonymous:ip:1.2.3.4:users_me"] == 1
        assert fake_redis.store["gcra:rate_limit:anonymous:ip:1.2.3.4:__ip_aggregate__"] == 1

    @pytest.mark.asyncio
    async def test_the_aggregate_can_be_the_thing_that_rejects(self, fake_redis: Any) -> None:
        with patch("src.app.core.security.auto_ban.get_redis", return_value=fake_redis):
            for _ in range(3):
                _ban, rl, aggregate = await check_ban_and_rate_limit(
                    "5.6.7.8",
                    "rate_limit:anonymous:ip:5.6.7.8:some_path",
                    limit=100,
                    period=60,
                    aggregate_limit=3,
                    aggregate_period=60,
                )
            assert rl[0] is False
            assert aggregate is not None and aggregate[0] is False

            _ban, rl, aggregate = await check_ban_and_rate_limit(
                "5.6.7.8",
                "rate_limit:anonymous:ip:5.6.7.8:some_path",
                limit=100,
                period=60,
                aggregate_limit=3,
                aggregate_period=60,
            )
        assert aggregate is not None
        assert aggregate[0] is True, "the 4th request trips the aggregate ceiling"
        assert rl[0] is True, "the aggregate rejection is the verdict"
        assert aggregate[2] > 0, "a rejection carries a Retry-After"

    @pytest.mark.asyncio
    async def test_disabled_ceiling_does_not_touch_the_aggregate_key(self, fake_redis: Any) -> None:
        with patch("src.app.core.security.auto_ban.get_redis", return_value=fake_redis):
            _ban, _rl, aggregate = await check_ban_and_rate_limit(
                "9.9.9.9", "rate_limit:anonymous:ip:9.9.9.9:p", limit=10, period=60
            )
        assert aggregate is None, "no fold was requested"
        assert not any("__ip_aggregate__" in key for key in fake_redis.store)

    @pytest.mark.asyncio
    async def test_adr0015_the_aggregate_is_not_consumed_when_the_per_path_leg_rejects(
        self, fake_redis: Any
    ) -> None:
        """The decision's own oracle, restated as a test."""
        with patch("src.app.core.security.auto_ban.get_redis", return_value=fake_redis):
            for _ in range(2):
                await check_ban_and_rate_limit(
                    "4.4.4.4",
                    "rate_limit:anonymous:ip:4.4.4.4:p",
                    limit=1,
                    period=60,
                    aggregate_limit=100,
                    aggregate_period=60,
                )
            aggregate_key = "gcra:rate_limit:anonymous:ip:4.4.4.4:__ip_aggregate__"
            after_first = fake_redis.store[aggregate_key]
            # The per-path bucket is exhausted (limit 1); this call rejects
            # BEFORE the aggregate leg runs.
            await check_ban_and_rate_limit(
                "4.4.4.4",
                "rate_limit:anonymous:ip:4.4.4.4:p",
                limit=1,
                period=60,
                aggregate_limit=100,
                aggregate_period=60,
            )
        assert fake_redis.store[aggregate_key] == after_first, "a rejected request must not spend the aggregate"

    @pytest.mark.asyncio
    async def test_a_banned_ip_consumes_nothing(self, fake_redis: Any) -> None:
        fake_redis.store["security:ban:3.3.3.3"] = "spam"
        with patch("src.app.core.security.auto_ban.get_redis", return_value=fake_redis):
            ban_info, _rl, _aggregate = await check_ban_and_rate_limit(
                "3.3.3.3", "rate_limit:anonymous:ip:3.3.3.3:p", limit=10, period=60
            )
        assert ban_info.is_banned is True
        assert not any(key.startswith("gcra:") for key in fake_redis.store)


class TestF10AdmissionWiring:
    """Admission passes the ceiling to the composite exactly when it applies."""

    @pytest.mark.asyncio
    async def test_anonymous_request_folds_the_ceiling(self) -> None:
        recorded: dict[str, Any] = {}

        async def _fake_composite(client_ip: str, key: str, limit: int, period: int, **kwargs: Any) -> Any:
            recorded.update(kwargs)
            from src.app.core.security.auto_ban import BanInfo

            return BanInfo(is_banned=False), (False, limit - 1, 0, False), None

        request = _request()
        with (
            patch("src.app.core.security.request_admission.check_ban_and_rate_limit", _fake_composite),
            patch("src.app.core.security.request_admission.uses_gcra", return_value=True),
            patch(
                "src.app.core.security.request_admission.settings"
            ) as settings_mock,
        ):
            settings_mock.security.AUTO_BAN_ENABLED = True
            settings_mock.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
            settings_mock.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = (300, 3600)
            settings_mock.rate_limit.DEFAULT_ANONYMOUS_LIMIT = (60, 60)
            settings_mock.rate_limit.TIER_RATE_LIMITS = {}
            settings_mock.rate_limit.API_KEY_LIMIT = (100, 60)
            await RequestAdmissionService().evaluate(request)

        assert recorded.get("aggregate_limit") == 300
        assert recorded.get("aggregate_period") == 3600

    @pytest.mark.asyncio
    async def test_authenticated_request_does_not_fold(self) -> None:
        """An authed caller is keyed by user id and has its own tier budget."""
        recorded: dict[str, Any] = {}

        async def _fake_merged(**kwargs: Any) -> Any:
            from src.app.core.security.auto_ban import BanInfo

            return None, 60, 60, "gcra:rate_limit:user:1:p", BanInfo(is_banned=False), (False, 59, 0, False)

        request = _request(authorization="Bearer token")
        with (
            patch(
                "src.app.core.security.request_admission.check_ban_blacklist_and_rate_limit",
                AsyncMock(side_effect=_fake_merged),
            ),
            patch("src.app.core.security.request_admission.check_ban_and_rate_limit") as legacy,
            patch("src.app.core.security.request_admission.uses_gcra", return_value=True),
            patch("src.app.core.security.request_admission.get_cached_jwt_payload", return_value=None),
            patch(
                "src.app.core.security.request_admission.extract_jwt_claims_to_state",
                return_value={"sub": "1", "tier_name": "free", "tenant_id": "t", "jti": "j"},
            ),
            patch("src.app.core.security.request_admission.settings") as settings_mock,
        ):
            settings_mock.security.AUTO_BAN_ENABLED = True
            settings_mock.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
            settings_mock.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = (300, 3600)
            await RequestAdmissionService().evaluate(request)

        legacy.assert_not_called()
        assert not recorded


class TestF12ViolationStatsMget:
    @pytest.mark.asyncio
    async def test_one_mget_per_scan_page(self) -> None:
        redis = AsyncMock()
        redis.scan = AsyncMock(
            side_effect=[
                (7, [b"security:violations:a", b"security:violations:b"]),
                (0, [b"security:violations:c"]),
            ]
        )
        redis.mget = AsyncMock(side_effect=[[b"2", b"3"], [b"4"]])
        with patch("src.app.core.utils.cache.client", redis):
            stats = await get_violation_stats()

        assert stats == {"total_ips": 3, "total_violations": 9}
        assert redis.mget.await_count == 2, "one mget per page, not one GET per key"
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_page_with_no_fresh_keys_makes_no_read(self) -> None:
        redis = AsyncMock()
        redis.scan = AsyncMock(side_effect=[(1, [b"security:violations:a"]), (0, [b"security:violations:a"])])
        redis.mget = AsyncMock(return_value=[b"1"])
        with patch("src.app.core.utils.cache.client", redis):
            stats = await get_violation_stats()

        assert stats == {"total_ips": 1, "total_violations": 1}
        assert redis.mget.await_count == 1


def _request(headers: dict[str, str] | None = None, authorization: str | None = None) -> Any:
    """A minimal Starlette Request shaped like the admission seam expects."""
    from starlette.requests import Request

    raw = [(b"host", b"testserver"), (b"user-agent", b"pytest")]
    if authorization:
        raw.append((b"authorization", authorization.encode()))
    for name, value in (headers or {}).items():
        raw.append((name.encode(), value.encode()))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/users/me",
        "raw_path": b"/api/v1/users/me",
        "query_string": b"",
        "headers": raw,
        "client": ("1.2.3.4", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)
