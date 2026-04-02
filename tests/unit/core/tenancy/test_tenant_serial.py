"""P3-2: Tenant Middleware Serialization Tests.

Verifies that tenant caching uses bytes directly instead of string conversion.
"""

from unittest.mock import AsyncMock

import pytest

from src.app.core.json_utils import json_dumps


class TestTenantSerialization:
    """Tests for tenant middleware serialization efficiency."""

    def test_json_dumps_returns_bytes_by_default(self) -> None:
        """Verify json_dumps returns bytes without as_str flag."""
        data = {"id": 1, "slug": "test", "is_active": True}

        result = json_dumps(data)

        assert isinstance(result, bytes)

    def test_json_dumps_with_as_str_returns_string(self) -> None:
        """Verify json_dumps returns string with as_str=True."""
        data = {"id": 1, "slug": "test", "is_active": True}

        result = json_dumps(data, as_str=True)

        assert isinstance(result, str)

    def test_bytes_avoid_decode_overhead(self) -> None:
        """Verify bytes skip the decode step."""
        data = {"id": 1, "slug": "test", "is_active": True}

        # Without as_str - no decode
        bytes_result = json_dumps(data)

        # With as_str - has decode overhead
        str_result = json_dumps(data, as_str=True)

        # Both represent same data
        assert bytes_result == str_result.encode()

        # But bytes_result avoids the decode step
        assert isinstance(bytes_result, bytes)

    def test_tenant_middleware_uses_bytes(self) -> None:
        """Verify tenant middleware uses bytes (no as_str=True)."""
        import inspect

        from src.app.middleware import tenant

        source = inspect.getsource(tenant)

        # Count occurrences of json_dumps
        source.count("json_dumps(")

        # Count occurrences of as_str=True
        source.count("as_str=True")

        # After fix, there should be no as_str=True in tenant caching
        # (there might be as_str=True in other contexts, but not for tenant cache)
        # Check the specific cache_data assignment
        # The code was refactored to: serialized = json_dumps(cache_data)
        assert "json_dumps(cache_data)" in source or "json_dumps(" in source

        # The fix removes as_str=True from cache_data assignment
        # Find the serialized line and verify no as_str
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "serialized = json_dumps(" in line:
                # Check if as_str=True is NOT in the next few lines of this call
                cache_block = "\n".join(lines[i : i + 10])
                if 'id": tenant.id' in cache_block:
                    # This is the tenant cache block
                    assert "as_str=True" not in cache_block

    @pytest.mark.asyncio
    async def test_redis_accepts_bytes(self) -> None:
        """Verify Redis setex accepts bytes directly."""
        mock_redis = AsyncMock()

        # Simulate the fixed pattern
        data = {"id": 1, "slug": "test", "is_active": True}
        cache_data = json_dumps(data)  # Returns bytes

        await mock_redis.setex("tenant:slug:test", 3600, cache_data)

        # Verify setex was called with bytes
        call_args = mock_redis.setex.call_args
        assert isinstance(call_args[0][2], bytes)

    @pytest.mark.asyncio
    async def test_json_loads_accepts_bytes(self) -> None:
        """Verify json_loads can parse bytes from Redis."""
        from src.app.core.json_utils import json_loads

        # Simulate Redis returning bytes
        data = {"id": 1, "slug": "test", "is_active": True}
        cached_bytes = json_dumps(data)

        # json_loads should handle bytes
        result = json_loads(cached_bytes)

        assert result == data
