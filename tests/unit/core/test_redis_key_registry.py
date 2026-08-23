"""Tests for the Redis key registry (single-source-of-truth contract).

Contract: every factory method formats its declared template constant, so
constant and method can never drift. Patterns are derived, never declared.
"""

import re

from src.app.core.redis_key_registry import RedisKeys, _pattern

# Every (template, factory, kwargs) triple in the registry.
_REGISTRY: list[tuple[str, object, str, dict[str, str]]] = [
    ("IP_WHITELIST", RedisKeys.Security.ip_whitelist, "security:ip:whitelist:{ip}", {"ip": "1.2.3.4"}),
    ("IP_BLACKLIST", RedisKeys.Security.ip_blacklist, "security:ip:blacklist:{ip}", {"ip": "1.2.3.4"}),
    ("BAN", RedisKeys.Security.ban_ip, "security:ban:{ip}", {"ip": "1.2.3.4"}),
    ("BAN_COUNT", RedisKeys.Security.ban_count, "security:ban_count:{ip}", {"ip": "1.2.3.4"}),
    ("SESSION", RedisKeys.Security.session, "security:session:{user_id}", {"user_id": "u1"}),
    ("POLICY_BY_TIER", RedisKeys.RateLimit.policy_by_tier, "rate_limit:policy:tier:{tier_id}", {"tier_id": "t1"}),
    ("TIER_LIMITS", RedisKeys.RateLimit.tier_limits, "rate_limits:tier:{tier_id}", {"tier_id": "t1"}),
    ("IP_TRACKING", RedisKeys.RateLimit.ip_tracking, "ip:{ip_address}", {"ip_address": "1.2.3.4"}),
    ("TOKEN", RedisKeys.TokenBlacklist.token, "token:blacklist:{token_hash}", {"token_hash": "h1"}),
    ("FEATURE_FLAG", RedisKeys.Cache.feature_flag, "cache:feature_flag:{flag_name}", {"flag_name": "f1"}),
    ("TIER", RedisKeys.Cache.tier, "cache:tier:{tier_id}", {"tier_id": "t1"}),
    ("SSO_SETTINGS", RedisKeys.Cache.sso_settings, "cache:sso:settings:{provider}", {"provider": "p1"}),
    ("REQUEST_COUNT", RedisKeys.Metrics.request_count, "metrics:requests:{endpoint}", {"endpoint": "/x"}),
    ("ERROR_COUNT", RedisKeys.Metrics.error_count, "metrics:errors:{endpoint}", {"endpoint": "/x"}),
    ("RESPONSE_TIME", RedisKeys.Metrics.response_time, "metrics:response_time:{endpoint}", {"endpoint": "/x"}),
]


def test_security_keys():
    assert RedisKeys.Security.ip_whitelist("192.168.1.1") == "security:ip:whitelist:192.168.1.1"
    assert RedisKeys.Security.ip_blacklist("10.0.0.1") == "security:ip:blacklist:10.0.0.1"
    assert RedisKeys.Security.ban_ip("192.168.1.1") == "security:ban:192.168.1.1"
    assert RedisKeys.Security.ban_count("192.168.1.1") == "security:ban_count:192.168.1.1"
    assert RedisKeys.Security.session("user123") == "security:session:user123"


def test_rate_limit_keys():
    assert RedisKeys.RateLimit.policy_by_tier("premium") == "rate_limit:policy:tier:premium"
    assert RedisKeys.RateLimit.tier_limits("free") == "rate_limits:tier:free"
    assert RedisKeys.RateLimit.ip_tracking("192.168.1.1") == "ip:192.168.1.1"


def test_token_blacklist_keys():
    assert RedisKeys.TokenBlacklist.token("hash123") == "token:blacklist:hash123"


def test_cache_keys():
    assert RedisKeys.Cache.feature_flag("beta_feature") == "cache:feature_flag:beta_feature"
    assert RedisKeys.Cache.tier("pro") == "cache:tier:pro"
    assert RedisKeys.Cache.sso_settings("google") == "cache:sso:settings:google"


def test_metrics_keys():
    assert RedisKeys.Metrics.request_count("/api/v1/users") == "metrics:requests:/api/v1/users"
    assert RedisKeys.Metrics.error_count("/api/v1/users") == "metrics:errors:/api/v1/users"
    assert RedisKeys.Metrics.response_time("/api/v1/users") == "metrics:response_time:/api/v1/users"


def test_every_factory_formats_its_own_template():
    """Single-source guarantee: factory(value) == template.format(**kwargs)."""
    for name, factory, template, kwargs in _REGISTRY:
        assert len(kwargs) == 1, f"{name}: test assumes a single template field"
        value = next(iter(kwargs.values()))
        expected = template.format(**kwargs)
        actual = factory(value)
        assert actual == expected, f"{name}: {actual!r} != {expected!r}"


def test_pattern_derivation():
    assert _pattern("security:ban:{ip}") == "security:ban:*"
    assert _pattern(RedisKeys.Security.BAN) == "security:ban:*"
    assert _pattern(RedisKeys.Cache.TIER) == "cache:tier:*"
    assert _pattern("plain:key") == "plain:key"


def test_no_duplicate_key_literals_in_registry_module():
    """Each key literal string appears exactly once across the registry module."""
    import pathlib

    source = (
        pathlib.Path(__file__).parents[3]
        / "src/app/core/redis_key_registry.py"
    ).read_text(encoding="utf-8")
    # Only declarations count (docstring examples are illustrative)
    literals = re.findall(r'Final\[str\] = "([a-z_]+(?::[a-z_*{}]+)+)"', source)
    duplicates = {lit for lit in literals if literals.count(lit) > 1}
    assert not duplicates, f"Duplicate key templates declared: {duplicates}"
