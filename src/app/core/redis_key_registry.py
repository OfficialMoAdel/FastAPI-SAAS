"""Redis Key Registry.

Single source of truth for all Redis key patterns used throughout the
application. Every key is declared exactly once as a ``str.format`` template
constant; factory methods format that same constant, so the two can never
drift (ADR: Round 1 candidate #6).

Usage:
    from src.app.core.redis_key_registry import RedisKeys

    # Generate keys (formats the declared template)
    ban_key = RedisKeys.Security.ban_ip("192.168.1.1")

    # Derive a SCAN pattern from any template - no separate constant needed
    all_bans = await redis.scan(match=RedisKeys.Security.pattern(RedisKeys.Security.BAN))
"""

import re
from typing import Final

_TEMPLATE_FIELD = re.compile(r"\{[^}]+\}")


def _pattern(template: str) -> str:
    """Derive a SCAN glob pattern from a key template.

    Example:
        >>> _pattern("security:ban:{ip}")
        'security:ban:*'
    """
    return _TEMPLATE_FIELD.sub("*", template)


class RedisKeys:
    """Centralized Redis key registry.

    Organized by domain area for discoverability and maintenance.
    Each key template is declared once; factory classmethods format it.
    """

    class Security:
        """Security-related Redis keys."""

        IP_WHITELIST: Final[str] = "security:ip:whitelist:{ip}"
        IP_BLACKLIST: Final[str] = "security:ip:blacklist:{ip}"
        BAN: Final[str] = "security:ban:{ip}"
        BAN_COUNT: Final[str] = "security:ban_count:{ip}"
        SESSION: Final[str] = "security:session:{user_id}"

        @classmethod
        def ip_whitelist(cls, ip_address: str) -> str:
            """Generate whitelist key for an IP address."""
            return cls.IP_WHITELIST.format(ip=ip_address)

        @classmethod
        def ip_blacklist(cls, ip_address: str) -> str:
            """Generate blacklist key for an IP address."""
            return cls.IP_BLACKLIST.format(ip=ip_address)

        @classmethod
        def ban_ip(cls, ip_address: str) -> str:
            """Generate ban key for an IP address."""
            return cls.BAN.format(ip=ip_address)

        @classmethod
        def ban_count(cls, ip_address: str) -> str:
            """Generate ban count key for an IP address."""
            return cls.BAN_COUNT.format(ip=ip_address)

        @classmethod
        def session(cls, user_id: str | int) -> str:
            """Generate session key for a user."""
            return cls.SESSION.format(user_id=user_id)

    class RateLimit:
        """Rate limiting Redis keys."""

        POLICY_BY_TIER: Final[str] = "rate_limit:policy:tier:{tier_id}"
        TIER_LIMITS: Final[str] = "rate_limits:tier:{tier_id}"
        IP_TRACKING: Final[str] = "ip:{ip_address}"

        @classmethod
        def policy_by_tier(cls, tier_id: str | int) -> str:
            """Generate rate limit policy key for a tier."""
            return cls.POLICY_BY_TIER.format(tier_id=tier_id)

        @classmethod
        def tier_limits(cls, tier_id: str | int) -> str:
            """Generate tier limits key."""
            return cls.TIER_LIMITS.format(tier_id=tier_id)

        @classmethod
        def ip_tracking(cls, ip_address: str) -> str:
            """Generate IP-based rate limit tracking key."""
            return cls.IP_TRACKING.format(ip_address=ip_address)

    class TokenBlacklist:
        """Token blacklist Redis keys."""

        TOKEN: Final[str] = "token:blacklist:{token_hash}"

        @classmethod
        def token(cls, token_hash: str) -> str:
            """Generate token blacklist key."""
            return cls.TOKEN.format(token_hash=token_hash)

    class Cache:
        """General caching Redis keys."""

        FEATURE_FLAG: Final[str] = "cache:feature_flag:{flag_name}"
        TIER: Final[str] = "cache:tier:{tier_id}"
        SSO_SETTINGS: Final[str] = "cache:sso:settings:{provider}"

        @classmethod
        def feature_flag(cls, flag_name: str) -> str:
            """Generate feature flag cache key."""
            return cls.FEATURE_FLAG.format(flag_name=flag_name)

        @classmethod
        def tier(cls, tier_id: str | int) -> str:
            """Generate tier cache key."""
            return cls.TIER.format(tier_id=tier_id)

        @classmethod
        def sso_settings(cls, provider: str) -> str:
            """Generate SSO settings cache key."""
            return cls.SSO_SETTINGS.format(provider=provider)

    class Metrics:
        """Metrics and monitoring Redis keys."""

        REQUEST_COUNT: Final[str] = "metrics:requests:{endpoint}"
        ERROR_COUNT: Final[str] = "metrics:errors:{endpoint}"
        RESPONSE_TIME: Final[str] = "metrics:response_time:{endpoint}"

        @classmethod
        def request_count(cls, endpoint: str) -> str:
            """Generate request count key for an endpoint."""
            return cls.REQUEST_COUNT.format(endpoint=endpoint)

        @classmethod
        def error_count(cls, endpoint: str) -> str:
            """Generate error count key for an endpoint."""
            return cls.ERROR_COUNT.format(endpoint=endpoint)

        @classmethod
        def response_time(cls, endpoint: str) -> str:
            """Generate response time key for an endpoint."""
            return cls.RESPONSE_TIME.format(endpoint=endpoint)


# Convenience exports for common use cases
SecurityKeys = RedisKeys.Security
RateLimitKeys = RedisKeys.RateLimit
TokenBlacklistKeys = RedisKeys.TokenBlacklist
CacheKeys = RedisKeys.Cache
MetricsKeys = RedisKeys.Metrics

__all__ = [
    "CacheKeys",
    "MetricsKeys",
    "RateLimitKeys",
    "RedisKeys",
    "SecurityKeys",
    "TokenBlacklistKeys",
]
