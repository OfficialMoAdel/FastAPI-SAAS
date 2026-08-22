"""Unit tests for auto-ban service.

Tests cover:
- Violation tracking and incrementing
- Ban info retrieval
- Ban/unban operations
- Escalation logic
- Threshold checking
- Statistics gathering
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.auto_ban import (
    BanInfo,
    ban_ip,
    check_and_auto_ban,
    get_all_banned_ips,
    get_ban_info,
    get_violation_stats,
    increment_violations,
    is_ip_banned,
    reset_violations,
    unban_ip,
)
from tests.conftest import patch_setting


@pytest.fixture
def mock_redis():
    """Create mock Redis client."""
    redis = AsyncMock()

    # Setup pipeline to support async with
    pipeline_mock = AsyncMock()
    pipeline_mock.__aenter__.return_value = pipeline_mock
    # By default, mock execute to return what incr would return
    pipeline_mock.execute.return_value = [1]
    redis.pipeline = MagicMock(return_value=pipeline_mock)

    # increment_violations prefers the atomic EVAL (Lua) path. Force the
    # pipeline fallback branch so pipeline-based assertions are deterministic.
    redis.eval = AsyncMock(side_effect=NotImplementedError("EVAL not supported by test mock"))

    return redis


@pytest.fixture
def sample_ip():
    """Sample IP address for tests."""
    return "192.168.1.100"


class TestIncrementViolations:
    """Test violation counter incrementing."""

    @pytest.mark.asyncio
    async def test_increment_violations_first_time(self, mock_redis, sample_ip):
        """Test incrementing violations for new IP."""
        mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [1]
        mock_redis.expire = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=True)

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            count = await increment_violations(sample_ip)

        assert count == 1
        pipe_mock = mock_redis.pipeline.return_value.__aenter__.return_value
        pipe_mock.incr.assert_called_once_with(f"security:violations:{sample_ip}")
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_increment_violations_multiple(self, mock_redis, sample_ip):
        """Test incrementing violations multiple times."""
        mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [5]
        mock_redis.expire = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=True)

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            count = await increment_violations(sample_ip)

        assert count == 5
        mock_redis.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_increment_violations_uses_atomic_eval(self, mock_redis, sample_ip):
        """Test that the atomic EVAL (Lua INCR + conditional EXPIRE) path is preferred."""
        mock_redis.eval = AsyncMock(return_value=1)

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            count = await increment_violations(sample_ip)

        assert count == 1
        args = mock_redis.eval.call_args.args
        assert "INCR" in args[0]
        assert args[1] == 1
        assert args[2] == f"security:violations:{sample_ip}"

    @pytest.mark.asyncio
    async def test_increment_violations_redis_unavailable(self, sample_ip):
        """Test handling when Redis is unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            count = await increment_violations(sample_ip)

        assert count == 0

    @pytest.mark.asyncio
    async def test_increment_violations_redis_error(self, mock_redis, sample_ip):
        """Test handling Redis errors gracefully."""
        mock_redis.pipeline.return_value.__aenter__.return_value.execute.side_effect = Exception("Redis error")

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            count = await increment_violations(sample_ip)

        assert count == 0


class TestResetViolations:
    """Test violation counter reset."""

    @pytest.mark.asyncio
    async def test_reset_violations_success(self, mock_redis, sample_ip):
        """Test successful violation reset."""
        mock_redis.delete.return_value = 1

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            result = await reset_violations(sample_ip)

        assert result is True
        mock_redis.delete.assert_called_once_with(f"security:violations:{sample_ip}")

    @pytest.mark.asyncio
    async def test_reset_violations_redis_unavailable(self, sample_ip):
        """Test reset when Redis unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            result = await reset_violations(sample_ip)

        assert result is False


class TestGetBanInfo:
    """Test ban information retrieval."""

    @pytest.mark.asyncio
    async def test_get_ban_info_not_banned(self, mock_redis, sample_ip):
        """Test getting info for non-banned IP."""
        mock_redis.get.return_value = None

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            info = await get_ban_info(sample_ip)

        assert info.is_banned is False
        assert info.ban_count == 0

    @pytest.mark.asyncio
    async def test_get_ban_info_banned(self, mock_redis, sample_ip):
        """Test getting info for banned IP."""
        mock_redis.get.side_effect = [
            b"Rate limit exceeded",  # ban data
            None,  # ban count
        ]
        mock_redis.ttl.return_value = 3600  # 1 hour remaining

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            info = await get_ban_info(sample_ip)

        assert info.is_banned is True
        assert info.reason == "Rate limit exceeded"
        assert info.expires_at is not None
        assert info.ban_count == 1

    @pytest.mark.asyncio
    async def test_get_ban_info_with_historical_count(self, mock_redis, sample_ip):
        """Test getting ban info with historical ban count."""
        mock_redis.get.side_effect = [
            b"Rate limit exceeded",
            b"3",  # Has been banned 3 times before
        ]
        mock_redis.ttl.return_value = 3600

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            info = await get_ban_info(sample_ip)

        assert info.is_banned is True
        assert info.ban_count == 3

    @pytest.mark.asyncio
    async def test_get_ban_info_redis_unavailable(self, sample_ip):
        """Test ban info when Redis unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            info = await get_ban_info(sample_ip)

        assert info.is_banned is False
        assert info.ban_count == 0


class TestBanIP:
    """Test IP banning functionality."""

    @pytest.mark.asyncio
    async def test_ban_ip_success(self, mock_redis, sample_ip):
        """Test successful IP ban."""
        # get is called twice in get_ban_info: once for ban_key, once for count_key
        mock_redis.get.side_effect = [None, None]  # Not banned, no historical count

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True, "AUTO_BAN_DURATION", 3600, "AUTO_BAN_ESCALATION", False),
            patch("src.app.core.security.auto_ban.log_security_event"),
        ):
            result = await ban_ip(sample_ip)

        assert result is True
        mock_redis.set.assert_called_once()
        mock_redis.incr.assert_called_once()

    @pytest.mark.asyncio
    async def test_ban_ip_disabled(self, mock_redis, sample_ip):
        """Test ban when auto-ban is disabled."""
        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", False),
        ):
            result = await ban_ip(sample_ip)

        assert result is False

    @pytest.mark.asyncio
    async def test_ban_ip_redis_unavailable(self, sample_ip):
        """Test ban when Redis unavailable."""
        with (
            patch("src.app.core.security.auto_ban.redis_client", None),
            patch_setting("AUTO_BAN_ENABLED", True),
        ):
            result = await ban_ip(sample_ip)

        assert result is False

    @pytest.mark.asyncio
    async def test_ban_ip_with_escalation(self, mock_redis, sample_ip):
        """Test ban duration escalation for repeat offenders."""
        # get is called twice in get_ban_info:
        # 1. ban_key -> None (not currently banned)
        # 2. count_key -> b"2" (2 previous bans)
        mock_redis.get.side_effect = [None, b"2"]

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_DURATION", 3600),
            patch_setting("AUTO_BAN_ESCALATION", True),
        ):
            result = await ban_ip(sample_ip)

        assert result is True
        # Duration should be 3600 * 2^2 = 14400 (4 hours)
        call_args = mock_redis.set.call_args
        assert call_args.kwargs["ex"] == 14400

    @pytest.mark.asyncio
    async def test_ban_ip_custom_duration(self, mock_redis, sample_ip):
        """Test ban with custom duration."""
        mock_redis.get.side_effect = [None, None]  # Not banned, no historical count

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_ESCALATION", False),
        ):
            result = await ban_ip(sample_ip, duration=7200, reason="Custom reason")

        assert result is True
        call_args = mock_redis.set.call_args
        assert call_args.kwargs["ex"] == 7200
        assert call_args.args[1] == "Custom reason"

    @pytest.mark.asyncio
    async def test_ban_ip_escalation_disabled(self, mock_redis, sample_ip):
        """Test that escalation respects settings."""
        mock_redis.get.side_effect = [None, b"5"]  # Not banned, 5 previous bans

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_DURATION", 3600),
            patch_setting("AUTO_BAN_ESCALATION", False),
        ):
            result = await ban_ip(sample_ip)

        assert result is True
        # Duration should remain 3600 (no escalation)
        call_args = mock_redis.set.call_args
        assert call_args.kwargs["ex"] == 3600


class TestUnbanIP:
    """Test IP unbanning functionality."""

    @pytest.mark.asyncio
    async def test_unban_ip_success(self, mock_redis, sample_ip):
        """Test successful unban."""
        mock_redis.delete.return_value = 1

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch("src.app.core.security.auto_ban.log_security_event"),
        ):
            result = await unban_ip(sample_ip)

        assert result is True
        mock_redis.delete.assert_called_once_with(f"security:ban:{sample_ip}")

    @pytest.mark.asyncio
    async def test_unban_ip_not_banned(self, mock_redis, sample_ip):
        """Test unban when IP wasn't banned."""
        mock_redis.delete.return_value = 0

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            result = await unban_ip(sample_ip)

        assert result is True

    @pytest.mark.asyncio
    async def test_unban_ip_redis_unavailable(self, sample_ip):
        """Test unban when Redis unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            result = await unban_ip(sample_ip)

        assert result is False


class TestIsIPBanned:
    """Test IP banned status check."""

    @pytest.mark.asyncio
    async def test_is_ip_banned_true(self, mock_redis, sample_ip):
        """Test checking banned IP."""
        # get called for ban_key, then count_key
        mock_redis.get.side_effect = [b"Banned", b"1"]
        mock_redis.ttl.return_value = 1800

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            result = await is_ip_banned(sample_ip)

        assert result is True

    @pytest.mark.asyncio
    async def test_is_ip_banned_false(self, mock_redis, sample_ip):
        """Test checking non-banned IP."""
        mock_redis.get.return_value = None  # Not banned

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            result = await is_ip_banned(sample_ip)

        assert result is False


class TestCheckAndAutoBan:
    """Test automatic ban checking."""

    @pytest.mark.asyncio
    async def test_check_and_auto_ban_threshold_not_met(self, mock_redis, sample_ip):
        """Test no ban when threshold not met."""
        mock_pipeline = mock_redis.pipeline.return_value.__aenter__.return_value
        mock_pipeline.execute.return_value = [3]  # Below threshold of 5

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_THRESHOLD", 5),
        ):
            banned, count = await check_and_auto_ban(sample_ip)

        assert banned is False
        assert count == 3

    @pytest.mark.asyncio
    async def test_check_and_auto_ban_threshold_exceeded(self, mock_redis, sample_ip):
        """Test ban when threshold exceeded."""
        mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [5]  # At threshold
        mock_redis.get.return_value = None

        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_THRESHOLD", 5),
            patch_setting("AUTO_BAN_DURATION", 3600),
        ):
            banned, count = await check_and_auto_ban(sample_ip)

        assert banned is True
        assert count == 5

    @pytest.mark.asyncio
    async def test_check_and_auto_ban_disabled(self, sample_ip):
        """Test no action when auto-ban disabled."""
        with patch_setting("AUTO_BAN_ENABLED", False):
            banned, count = await check_and_auto_ban(sample_ip)

        assert banned is False
        assert count == 0


class TestGetAllBannedIPs:
    """Test retrieving all banned IPs."""

    @pytest.mark.asyncio
    async def test_get_all_banned_ips(self, mock_redis):
        """Test getting list of banned IPs."""

        mock_redis.scan.side_effect = [
            (0, [b"security:ban:192.168.1.1", b"security:ban:192.168.1.2"]),
        ]

        # We queue calls for get(ban), ttl(ban), get(count) for each IP
        mock_redis.get.side_effect = [
            b"Rate limit exceeded",  # ban data for IP 1
            b"1",  # count for IP 1
            b"Too many requests",  # ban data for IP 2
            b"2",  # count for IP 2
        ]

        mock_redis.ttl.return_value = 1800

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            banned = await get_all_banned_ips()

        assert len(banned) == 2
        assert banned[0]["ip"] == "192.168.1.1"
        assert banned[1]["ip"] == "192.168.1.2"

    @pytest.mark.asyncio
    async def test_get_all_banned_ips_empty(self, mock_redis):
        """Test getting banned IPs when none exist."""
        mock_redis.scan.return_value = (0, [])

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            banned = await get_all_banned_ips()

        assert banned == []

    @pytest.mark.asyncio
    async def test_get_all_banned_ips_redis_unavailable(self):
        """Test when Redis unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            banned = await get_all_banned_ips()

        assert banned == []


class TestGetViolationStats:
    """Test violation statistics."""

    @pytest.mark.asyncio
    async def test_get_violation_stats_specific_ip(self, mock_redis, sample_ip):
        """Test getting stats for specific IP."""
        mock_redis.get.return_value = b"10"

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            stats = await get_violation_stats(sample_ip)

        assert stats["ip"] == sample_ip
        assert stats["violations"] == 10

    @pytest.mark.asyncio
    async def test_get_violation_stats_all(self, mock_redis):
        """Test getting stats for all IPs."""
        mock_redis.scan.side_effect = [
            (0, [b"security:violations:1.1.1.1", b"security:violations:2.2.2.2"]),
        ]
        mock_redis.get.side_effect = [b"5", b"3"]

        with patch("src.app.core.security.auto_ban.redis_client", mock_redis):
            stats = await get_violation_stats()

        assert stats["total_ips"] == 2
        assert stats["total_violations"] == 8

    @pytest.mark.asyncio
    async def test_get_violation_stats_redis_unavailable(self):
        """Test stats when Redis unavailable."""
        with patch("src.app.core.security.auto_ban.redis_client", None):
            stats = await get_violation_stats()

        assert stats == {"total_ips": 0, "total_violations": 0}


class TestBanInfoDataclass:
    """Test BanInfo dataclass."""

    def test_ban_info_defaults(self):
        """Test BanInfo default values."""
        info = BanInfo(is_banned=False)

        assert info.is_banned is False
        assert info.reason is None
        assert info.expires_at is None
        assert info.ban_count == 0

    def test_ban_info_full(self):
        """Test BanInfo with all values."""
        info = BanInfo(is_banned=True, reason="Test ban", expires_at=1234567890.0, ban_count=3)

        assert info.is_banned is True
        assert info.reason == "Test ban"
        assert info.expires_at == 1234567890.0
        assert info.ban_count == 3


class TestIntegrationScenarios:
    """Test complete ban/unban workflows."""

    @pytest.mark.asyncio
    async def test_full_ban_workflow(self, mock_redis, sample_ip):
        """Test complete ban workflow from violation to ban."""
        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting(
                "AUTO_BAN_ENABLED",
                True,
                "AUTO_BAN_THRESHOLD",
                3,
                "AUTO_BAN_DURATION",
                3600,
                "AUTO_BAN_ESCALATION",
                False,
            ),
        ):
            # Violation 1 - no ban
            mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [1]
            banned, count = await check_and_auto_ban(sample_ip)
            assert banned is False
            assert count == 1

            # Violation 2 - no ban
            mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [2]
            banned, count = await check_and_auto_ban(sample_ip)
            assert banned is False
            assert count == 2

            # Violation 3 - triggers ban (execute returns [3, True])
            mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [3]
            # When ban_ip is called, it increments historical count
            mock_redis.incr = AsyncMock(return_value=1)  # [ban_count]
            mock_redis.get.side_effect = [None, None]  # Not banned, no count
            mock_redis.setex = AsyncMock(return_value=True)
            banned, count = await check_and_auto_ban(sample_ip)
            assert banned is True
            assert count == 3

    @pytest.mark.asyncio
    async def test_escalating_bans(self, mock_redis, sample_ip):
        """Test that repeated bans escalate duration."""
        with (
            patch("src.app.core.security.auto_ban.redis_client", mock_redis),
            patch_setting("AUTO_BAN_ENABLED", True),
            patch_setting("AUTO_BAN_DURATION", 3600),
            patch_setting("AUTO_BAN_ESCALATION", True),
        ):
            # First ban - no escalation (ban_count = 0)
            mock_redis.get.side_effect = [None, None]  # Not banned, no count
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.incr = AsyncMock(return_value=1)  # ban_count
            await ban_ip(sample_ip)
            assert mock_redis.set.call_args.kwargs["ex"] == 3600

            # Second ban - 2x escalation (7200 seconds) - ban_count = 1
            mock_redis.get.side_effect = [None, b"1"]  # Not banned, count = 1
            mock_redis.incr = AsyncMock(return_value=2)
            await ban_ip(sample_ip)
            assert mock_redis.set.call_args.kwargs["ex"] == 7200

            # Third ban - 4x escalation (14400 seconds) - ban_count = 2
            mock_redis.get.side_effect = [None, b"2"]  # Not banned, count = 2
            mock_redis.incr = AsyncMock(return_value=3)
            await ban_ip(sample_ip)
            assert mock_redis.set.call_args.kwargs["ex"] == 14400
