from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from uuid6 import uuid7

import pytest

from src.app.modules.users.models import User
from tests.conftest import MockUser


@pytest.mark.asyncio
async def test_cache_user_uses_pipeline():
    """Test that _cache_user uses a Redis pipeline for optimization."""
    from src.app.modules.users.service import (
        UserService,
        _user_cache_key,
        _user_email_cache_key,
        _user_username_cache_key,
    )

    # Setup
    mock_db = AsyncMock()

    # Create user with UUID tenant_id
    tenant_id = uuid7()
    user = MagicMock(spec=User)
    user.id = 1  # Must be int
    user.email = "test@example.com"
    user.username = "testuser"
    user.name = "Test User"
    user.is_deleted = False
    user.is_superuser = False
    user.is_email_verified = True
    user.profile_image_url = "https://example.com/profile.jpg"
    user.hashed_password = "hash"
    user.created_at = MagicMock()
    user.created_at.isoformat.return_value = "2023-01-01T00:00:00"
    user.updated_at = None
    user.tenant_id = tenant_id
    user.tier_id = 1

    # Mock tenant context
    mock_tenant = MagicMock()
    mock_tenant.id = tenant_id

    # Mock cache with pipeline
    mock_cache = MagicMock()
    mock_redis = MagicMock()
    mock_cache.client = mock_redis

    # Create mock pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.execute = AsyncMock()
    mock_pipeline.__aenter__ = AsyncMock(return_value=mock_pipeline)
    mock_pipeline.__aexit__ = AsyncMock(return_value=None)
    mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

    # Patch both the context module and the service's imported reference
    with patch("src.app.core.tenancy.context.get_current_tenant", return_value=mock_tenant):
        with patch("src.app.modules.users.service.get_current_tenant", return_value=mock_tenant):
            with patch("src.app.modules.users.service.cache", mock_cache):
                service = UserService(mock_db)
                await service._cache_user(user)

                # VERIFICATION
                mock_redis.pipeline.assert_called_once_with(transaction=False)
                assert mock_pipeline.setex.call_count == 3
                mock_pipeline.execute.assert_called_once()

                # Check keys - must be inside patch context for tenant-aware key functions
                calls = mock_pipeline.setex.call_args_list
                keys = [call.args[0] for call in calls]
                assert _user_cache_key(user.id) in keys
                assert _user_email_cache_key(user.email) in keys
                assert _user_username_cache_key(user.username) in keys
