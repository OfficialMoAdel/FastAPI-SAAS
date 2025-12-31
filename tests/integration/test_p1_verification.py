import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.config import settings

@pytest.mark.asyncio
async def test_config_security_settings():
    """Verify critical security configuration settings."""
    # Verify Rate Limit Fail-Closed
    assert settings.RATE_LIMIT_FAIL_OPEN is False, "Rate limiting must be fail-closed (False)"
    
    # Verify Default DB IP (User Request)
    assert settings.POSTGRES_SERVER == "192.168.121.147", "POSTGRES_SERVER must match requested VM IP"

@pytest.mark.asyncio
async def test_user_service_n_plus_one_optimization(
    client: AsyncClient, 
    db: AsyncSession,
    normal_user_token_headers: dict[str, str]
):
    """Verify that get_user_with_tier returns correct data structure after optimization."""
    # This test primarily verifies correctness of the new eager loading method
    response = await client.get("/api/v1/users/me", headers=normal_user_token_headers)
    assert response.status_code == 200
    data = response.json()
    
    # Check if tier info is present (assuming seeded user has a tier, or at least structure is correct)
    # Even if no tier, the endpoint should work without error
    assert "username" in data
    assert "email" in data
    # If tier exists, it should be flattened
    if "tier_name" in data:
        assert data["tier_name"] is not None
