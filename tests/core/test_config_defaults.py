from src.app.core.config import R1UnifiedSettings


def test_rbac_cache_ttl_default():
    """Verify RBAC cache TTL default is 24 hours (86400 seconds)."""
    settings = R1UnifiedSettings()
    assert settings.RBAC_CACHE_TTL_SECONDS == 86400
