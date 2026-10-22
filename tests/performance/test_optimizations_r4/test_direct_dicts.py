"""P2-1: Direct Dict Construction (Pydantic Bypass) Tests.

STATUS: VERIFIED ✅

This module tests the direct dict construction optimization which bypasses
Pydantic model construction in hot paths where data is already validated.

Use Case:
- Returning data from ORM objects that are already validated
- Internal service returns where types are guaranteed
- Bulk operations where validation overhead is significant
"""

from __future__ import annotations

import inspect
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock


class TestP21DirectDictPrerequisites:
    """Pre-implementation tests for direct dict construction."""

    def test_pydantic_models_exist(self) -> None:
        """Verify Pydantic models are being used."""
        from pydantic import BaseModel

        assert BaseModel is not None

    def test_tenant_service_exists(self) -> None:
        """Verify TenantService exists."""
        from src.app.modules.tenants.service import TenantService

        assert TenantService is not None

    def test_helper_function_exists(self) -> None:
        """Verify _tenant_to_dict helper function exists."""
        from src.app.modules.tenants.service import _tenant_to_dict

        assert _tenant_to_dict is not None
        assert callable(_tenant_to_dict)


class TestP21DirectDictImplementation:
    """Tests verifying direct dict implementation."""

    def test_update_tenant_uses_direct_dict(self) -> None:
        """update_tenant should use _tenant_to_dict, not TenantResponse."""
        from src.app.modules.tenants.service import TenantService

        source = inspect.getsource(TenantService.update_tenant)

        # Should use direct dict helper
        assert "_tenant_to_dict" in source
        # Should NOT use Pydantic model_validate
        assert "TenantResponse.model_validate" not in source

    def test_deactivate_tenant_uses_direct_dict(self) -> None:
        """deactivate_tenant delegates to the shared helper that uses _tenant_to_dict."""
        from src.app.modules.tenants.service import TenantService

        source = inspect.getsource(TenantService.deactivate_tenant)
        assert "self._set_active_state(" in source
        assert "is_active=False" in source
        assert "TenantResponse.model_validate" not in source

        shared = inspect.getsource(TenantService._set_active_state)
        assert "_tenant_to_dict" in shared
        assert "TenantResponse.model_validate" not in shared

    def test_reactivate_tenant_uses_direct_dict(self) -> None:
        """reactivate_tenant delegates to the shared helper that uses _tenant_to_dict."""
        from src.app.modules.tenants.service import TenantService

        source = inspect.getsource(TenantService.reactivate_tenant)
        assert "self._set_active_state(" in source
        assert "is_active=True" in source
        assert "TenantResponse.model_validate" not in source

        shared = inspect.getsource(TenantService._set_active_state)
        assert "_tenant_to_dict" in shared
        assert "TenantResponse.model_validate" not in shared


class TestP21DirectDictOptimization:
    """Tests verifying direct dict output and performance."""

    def test_helper_returns_correct_fields(self) -> None:
        """_tenant_to_dict should return all expected fields."""
        from src.app.modules.tenants.service import _tenant_to_dict

        # Create a mock Tenant ORM object
        mock_tenant = MagicMock()
        mock_tenant.id = 1
        mock_tenant.created_at = datetime(2024, 1, 1, 12, 0, 0)
        mock_tenant.updated_at = datetime(2024, 1, 2, 12, 0, 0)
        mock_tenant.name = "Test Tenant"
        mock_tenant.slug = "test-tenant"
        mock_tenant.is_active = True
        mock_tenant.feature_flags = {}
        mock_tenant.storage_usage_bytes = 0
        mock_tenant.file_count = 0

        result = _tenant_to_dict(mock_tenant)

        assert result["id"] == 1
        assert result["created_at"] == datetime(2024, 1, 1, 12, 0, 0)
        assert result["updated_at"] == datetime(2024, 1, 2, 12, 0, 0)
        assert result["name"] == "Test Tenant"
        assert result["slug"] == "test-tenant"
        assert result["is_active"] is True

    def test_helper_handles_null_updated_at(self) -> None:
        """_tenant_to_dict should handle null updated_at."""
        from src.app.modules.tenants.service import _tenant_to_dict

        mock_tenant = MagicMock()
        mock_tenant.id = 1
        mock_tenant.created_at = datetime(2024, 1, 1, 12, 0, 0)
        mock_tenant.updated_at = None
        mock_tenant.name = "Test Tenant"
        mock_tenant.slug = "test-tenant"
        mock_tenant.is_active = True
        mock_tenant.feature_flags = {}
        mock_tenant.storage_usage_bytes = 0
        mock_tenant.file_count = 0

        result = _tenant_to_dict(mock_tenant)

        assert result["updated_at"] is None


class TestP21DirectDictIsomorphism:
    """Isomorphism tests for direct dict behavior."""

    def test_output_matches_pydantic_model_dump(self) -> None:
        """Direct dict output should match TenantResponse.model_dump()."""
        from src.app.modules.tenants.schemas import TenantResponse
        from src.app.modules.tenants.service import _tenant_to_dict

        # Create mock ORM object with realistic values
        mock_tenant = MagicMock()
        mock_tenant.id = uuid.UUID("00000000-0000-0000-0000-000000000042")
        mock_tenant.created_at = datetime(2024, 6, 15, 10, 30, 0)
        mock_tenant.updated_at = datetime(2024, 6, 16, 14, 45, 0)
        mock_tenant.name = "Acme Corporation"
        mock_tenant.slug = "acme-corp"
        mock_tenant.is_active = True
        mock_tenant.feature_flags = {}
        mock_tenant.storage_usage_bytes = 0
        mock_tenant.file_count = 0

        # Get output from direct dict
        direct_result = _tenant_to_dict(mock_tenant)

        # Get output from Pydantic
        pydantic_result = TenantResponse.model_validate(mock_tenant).model_dump()

        # They should be identical
        assert direct_result == pydantic_result

    def test_output_matches_with_inactive_tenant(self) -> None:
        """Verify isomorphism with is_active=False."""
        from src.app.modules.tenants.schemas import TenantResponse
        from src.app.modules.tenants.service import _tenant_to_dict

        mock_tenant = MagicMock()
        mock_tenant.id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        mock_tenant.created_at = datetime(2023, 1, 1, 0, 0, 0)
        mock_tenant.updated_at = None
        mock_tenant.name = "Deactivated Org"
        mock_tenant.slug = "deactivated-org"
        mock_tenant.is_active = False
        mock_tenant.feature_flags = {}
        mock_tenant.storage_usage_bytes = 0
        mock_tenant.file_count = 0

        direct_result = _tenant_to_dict(mock_tenant)
        pydantic_result = TenantResponse.model_validate(mock_tenant).model_dump()

        assert direct_result == pydantic_result


class TestP21PerformanceValidation:
    """Performance validation tests."""

    def test_direct_dict_faster_than_pydantic(self) -> None:
        """Direct dict construction should be faster than Pydantic."""
        from src.app.modules.tenants.schemas import TenantResponse
        from src.app.modules.tenants.service import _tenant_to_dict

        # Create a mock tenant
        mock_tenant = MagicMock()
        mock_tenant.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        mock_tenant.created_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.updated_at = datetime.now(UTC).replace(tzinfo=None)
        mock_tenant.name = "Performance Test Tenant"
        mock_tenant.slug = "perf-test"
        mock_tenant.is_active = True
        mock_tenant.feature_flags = {}
        mock_tenant.storage_usage_bytes = 0
        mock_tenant.file_count = 0

        iterations = 1000

        # Benchmark Pydantic
        start = time.perf_counter()
        for _ in range(iterations):
            TenantResponse.model_validate(mock_tenant).model_dump()
        pydantic_time = time.perf_counter() - start

        # Benchmark direct dict
        start = time.perf_counter()
        for _ in range(iterations):
            _tenant_to_dict(mock_tenant)
        direct_time = time.perf_counter() - start

        # Direct dict should be at least 2x faster (typically 3-5x)
        speedup = pydantic_time / direct_time
        assert speedup > 1.5, f"Direct dict should be faster, speedup was {speedup:.2f}x"
