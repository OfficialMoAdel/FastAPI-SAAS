from src.app.core.redis_key_registry import RedisKeys


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
