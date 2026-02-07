"""Redis Key Registry.

Centralized registry for all Redis key patterns used throughout the application.
Provides consistent key naming, documentation, and helper methods for key generation.

Usage:
    from src.app.core.redis_key_registry import RedisKeys

    # Generate keys
    ban_key = RedisKeys.Security.ban_ip("192.168.1.1")
    whitelist_key = RedisKeys.Security.ip_whitelist("10.0.0.1")
    rate_limit_key = RedisKeys.RateLimit.policy_by_tier("premium")

    # Use patterns for scanning
    all_bans = await redis.scan(match=RedisKeys.Security.BAN_PATTERN)
"""

from typing import Final


class RedisKeys:
    """Centralized Redis key registry.

    Organized by domain area for discoverability and maintenance.
    All key patterns use consistent naming conventions.
    """

    class Security:
        """Security-related Redis keys."""

        # IP Filtering
        IP_WHITELIST_PREFIX: Final[str] = "security:ip:whitelist:{ip}"
        IP_BLACKLIST_PREFIX: Final[str] = "security:ip:blacklist:{ip}"
        IP_WHITELIST_PATTERN: Final[str] = "security:ip:whitelist:*"
        IP_BLACKLIST_PATTERN: Final[str] = "security:ip:blacklist:*"

        # Auto-ban system
        BAN_PREFIX: Final[str] = "security:ban:{ip}"
        BAN_COUNT_PREFIX: Final[str] = "security:ban_count:{ip}"
        BAN_PATTERN: Final[str] = "security:ban:*"
        BAN_COUNT_PATTERN: Final[str] = "security:ban_count:*"

        # Session management
        SESSION_PREFIX: Final[str] = "security:session:{user_id}"
        SESSION_PATTERN: Final[str] = "security:session:*"

        @staticmethod
        def ip_whitelist(ip_address: str) -> str:
            """Generate whitelist key for an IP address."""
            return f"security:ip:whitelist:{ip_address}"

        @staticmethod
        def ip_blacklist(ip_address: str) -> str:
            """Generate blacklist key for an IP address."""
            return f"security:ip:blacklist:{ip_address}"

        @staticmethod
        def ban_ip(ip_address: str) -> str:
            """Generate ban key for an IP address."""
            return f"security:ban:{ip_address}"

        @staticmethod
        def ban_count(ip_address: str) -> str:
            """Generate ban count key for an IP address."""
            return f"security:ban_count:{ip_address}"

        @staticmethod
        def session(user_id: str | int) -> str:
            """Generate session key for a user."""
            return f"security:session:{user_id}"

    class RateLimit:
        """Rate limiting Redis keys."""

        # Policy storage
        POLICY_BY_TIER_PREFIX: Final[str] = "rate_limit:policy:tier:{tier_id}"
        POLICY_INDEX_KEY: Final[str] = "rate_limit:policies:all"

        # Rate limit tracking
        TIER_LIMITS_PREFIX: Final[str] = "rate_limits:tier:{tier_id}"
        IP_TRACKING_PREFIX: Final[str] = "ip:{ip_address}"

        @staticmethod
        def policy_by_tier(tier_id: str | int) -> str:
            """Generate rate limit policy key for a tier."""
            return f"rate_limit:policy:tier:{tier_id}"

        @staticmethod
        def tier_limits(tier_id: str | int) -> str:
            """Generate tier limits key."""
            return f"rate_limits:tier:{tier_id}"

        @staticmethod
        def ip_tracking(ip_address: str) -> str:
            """Generate IP-based rate limit tracking key."""
            return f"ip:{ip_address}"

    class TokenBlacklist:
        """Token blacklist Redis keys."""

        TOKEN_PREFIX: Final[str] = "token:blacklist:{token_hash}"
        TOKEN_PATTERN: Final[str] = "token:blacklist:*"

        @staticmethod
        def token(token_hash: str) -> str:
            """Generate token blacklist key."""
            return f"token:blacklist:{token_hash}"

    class Cache:
        """General caching Redis keys."""

        # Feature flags
        FEATURE_FLAG_PREFIX: Final[str] = "cache:feature_flag:{flag_name}"
        FEATURE_FLAGS_INDEX: Final[str] = "cache:feature_flags:all"

        # Tiers
        TIER_PREFIX: Final[str] = "cache:tier:{tier_id}"
        TIERS_INDEX: Final[str] = "cache:tiers:all"

        # SSO settings
        SSO_SETTINGS_PREFIX: Final[str] = "cache:sso:settings:{provider}"
        SSO_SETTINGS_PATTERN: Final[str] = "cache:sso:settings:*"

        @staticmethod
        def feature_flag(flag_name: str) -> str:
            """Generate feature flag cache key."""
            return f"cache:feature_flag:{flag_name}"

        @staticmethod
        def tier(tier_id: str | int) -> str:
            """Generate tier cache key."""
            return f"cache:tier:{tier_id}"

        @staticmethod
        def sso_settings(provider: str) -> str:
            """Generate SSO settings cache key."""
            return f"cache:sso:settings:{provider}"

    class Metrics:
        """Metrics and monitoring Redis keys."""

        # Request tracking
        REQUEST_COUNT_PREFIX: Final[str] = "metrics:requests:{endpoint}"
        ERROR_COUNT_PREFIX: Final[str] = "metrics:errors:{endpoint}"

        # Performance
        RESPONSE_TIME_PREFIX: Final[str] = "metrics:response_time:{endpoint}"

        @staticmethod
        def request_count(endpoint: str) -> str:
            """Generate request count key for an endpoint."""
            return f"metrics:requests:{endpoint}"

        @staticmethod
        def error_count(endpoint: str) -> str:
            """Generate error count key for an endpoint."""
            return f"metrics:errors:{endpoint}"

        @staticmethod
        def response_time(endpoint: str) -> str:
            """Generate response time key for an endpoint."""
            return f"metrics:response_time:{endpoint}"


# Convenience exports for common use cases
SecurityKeys = RedisKeys.Security
RateLimitKeys = RedisKeys.RateLimit
TokenBlacklistKeys = RedisKeys.TokenBlacklist
CacheKeys = RedisKeys.Cache
MetricsKeys = RedisKeys.Metrics
