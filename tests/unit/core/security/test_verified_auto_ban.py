"""Verified auto-ban fixes: F9 publish-failure success, F19 SCAN dedupe, F20 prefix strip."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.auto_ban import ban_ip, get_all_banned_ips, get_violation_stats, unban_ip
from tests.conftest import patch_setting


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    pipeline_mock = AsyncMock()
    pipeline_mock.__aenter__.return_value = pipeline_mock
    pipeline_mock.execute.return_value = [1]
    redis.pipeline = MagicMock(return_value=pipeline_mock)
    redis.evalsha = AsyncMock(return_value=[1, 1])
    redis.eval = AsyncMock(side_effect=NotImplementedError("EVAL not supported by test mock"))
    return redis


class TestF9PublishFailureKeepsSuccess:
    @pytest.mark.asyncio
    async def test_ban_ip_true_when_fanout_fails(self, mock_redis):
        """F9: Lua ban succeeded — a publish error must not flip the result to False."""
        mock_redis.get.side_effect = [None, None]
        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True, "AUTO_BAN_DURATION", 3600, "AUTO_BAN_ESCALATION", False),
            patch("src.app.core.security.auto_ban.log_security_event"),
            patch(
                "src.app.core.security.auto_ban._publish_ban_l1_invalidation",
                AsyncMock(side_effect=RuntimeError("pub down")),
            ),
        ):
            assert await ban_ip("10.0.0.1") is True

    @pytest.mark.asyncio
    async def test_unban_ip_true_when_fanout_fails(self, mock_redis):
        """F9: delete succeeded — a publish error must not flip the result to False."""
        mock_redis.delete.return_value = 1
        with (
            patch("src.app.core.utils.cache.client", mock_redis),
            patch("src.app.core.security.auto_ban.log_security_event"),
            patch(
                "src.app.core.security.auto_ban._publish_ban_l1_invalidation",
                AsyncMock(side_effect=RuntimeError("pub down")),
            ),
        ):
            assert await unban_ip("10.0.0.1") is True


class TestF20PrefixStrip:
    @pytest.mark.asyncio
    async def test_ipv6_key_parses_to_full_address(self, mock_redis):
        """F20: removeprefix strips only the leading prefix; IPv6 colons survive."""
        mock_redis.scan.return_value = (0, [b"security:ban:2001:db8::1"])
        mock_redis.get = AsyncMock(side_effect=[b"spam", b"1"])
        mock_redis.ttl = AsyncMock(return_value=300)
        with patch("src.app.core.utils.cache.client", mock_redis):
            banned = await get_all_banned_ips()
        assert [b["ip"] for b in banned] == ["2001:db8::1"]


class TestF19ScanDedupe:
    @pytest.mark.asyncio
    async def test_duplicate_keys_across_iterations_counted_once(self, mock_redis):
        """F19: a key repeated by SCAN on a later page counts once, GET once."""
        dup = "security:violations:9.9.9.9"
        mock_redis.scan.side_effect = [(1, [dup]), (0, [dup])]
        # F-12: the dedupe means the second page contributes no key, so mget
        # is called ONCE with ONE key -- the same single read the per-key loop
        # made, now batched.
        mock_redis.mget = AsyncMock(return_value=[b"3"])
        with patch("src.app.core.utils.cache.client", mock_redis):
            stats = await get_violation_stats()
        assert stats == {"total_ips": 1, "total_violations": 3}
        assert mock_redis.mget.await_count == 1
