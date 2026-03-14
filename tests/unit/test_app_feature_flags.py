"""Tests for the core FeatureFlagsService in src/app/feature_flags.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.feature_flags import FeatureFlagsService


class TestFeatureFlagsService:
    """Test FeatureFlagsService core functionality."""

    @pytest.fixture
    def service(self):
        """Create service instance."""
        return FeatureFlagsService()

    def test_service_initialization(self, service):
        """Test service initializes with session maker."""
        assert service._session_maker is not None


class TestIsEnabled:
    """Test is_enabled method."""

    @pytest.mark.asyncio
    async def test_is_enabled_returns_true_when_flag_is_set(self):
        """Test is_enabled returns True when flag is enabled."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {"my_flag": True}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.is_enabled("tenant-123", "my_flag")

            assert result is True

    @pytest.mark.asyncio
    async def test_is_enabled_returns_false_when_flag_not_set(self):
        """Test is_enabled returns False when flag is not set."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.is_enabled("tenant-123", "nonexistent_flag")

            assert result is False

    @pytest.mark.asyncio
    async def test_is_enabled_returns_default_when_flag_missing(self):
        """Test is_enabled returns default value when flag is not in flags."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {"other_flag": True}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.is_enabled("tenant-123", "missing_flag", default=True)

            assert result is True

    @pytest.mark.asyncio
    async def test_is_enabled_returns_false_for_tenant_not_found(self):
        """Test is_enabled returns default when tenant not found."""
        service = FeatureFlagsService()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.is_enabled("nonexistent-tenant", "some_flag")

            assert result is False

    @pytest.mark.asyncio
    async def test_is_enabled_converts_to_bool(self):
        """Test is_enabled properly converts flag value to bool."""
        service = FeatureFlagsService()

        # Test with various truthy/falsy values
        test_cases = [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("true", True),
            ("", False),
        ]

        for original_value, expected in test_cases:
            mock_tenant = MagicMock()
            mock_tenant.feature_flags = {"test_flag": original_value}

            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_tenant
            mock_session.execute = AsyncMock(return_value=mock_result)

            with patch.object(service, "_session_maker", return_value=mock_session):
                result = await service.is_enabled("tenant-123", "test_flag")
                assert result is expected, f"Failed for {original_value}"


class TestGetFlag:
    """Test get_flag method."""

    @pytest.mark.asyncio
    async def test_get_flag_returns_value(self):
        """Test get_flag returns the flag value."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {"my_flag": "custom_value"}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_flag("tenant-123", "my_flag")

            assert result == "custom_value"

    @pytest.mark.asyncio
    async def test_get_flag_returns_default_when_missing(self):
        """Test get_flag returns default when flag is missing."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_flag("tenant-123", "missing", default="fallback")

            assert result == "fallback"

    @pytest.mark.asyncio
    async def test_get_flag_returns_none_when_tenant_not_found(self):
        """Test get_flag returns None when tenant doesn't exist."""
        service = FeatureFlagsService()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_flag("nonexistent", "flag")

            assert result is None


class TestSetFlag:
    """Test set_flag method."""

    @pytest.mark.asyncio
    async def test_set_flag_returns_true_when_successful(self):
        """Test set_flag returns True on success."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.commit = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.set_flag("tenant-123", "new_flag", True)

            assert result is True
            assert mock_tenant.feature_flags["new_flag"] is True
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_flag_returns_false_when_tenant_not_found(self):
        """Test set_flag returns False when tenant doesn't exist."""
        service = FeatureFlagsService()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.set_flag("nonexistent", "flag", True)

            assert result is False

    @pytest.mark.asyncio
    async def test_set_flag_initializes_feature_flags_dict(self):
        """Test set_flag initializes feature_flags dict if None."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = None  # Not initialized

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.commit = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.set_flag("tenant-123", "flag", True)

            assert result is True
            assert mock_tenant.feature_flags == {"flag": True}


class TestGetAllFlags:
    """Test get_all_flags method."""

    @pytest.mark.asyncio
    async def test_get_all_flags_returns_dict(self):
        """Test get_all_flags returns all flags as dict."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {"flag1": True, "flag2": False, "flag3": "value"}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_all_flags("tenant-123")

            assert result == {"flag1": True, "flag2": False, "flag3": "value"}

    @pytest.mark.asyncio
    async def test_get_all_flags_returns_empty_when_no_flags(self):
        """Test get_all_flags returns empty dict when tenant has no flags."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_all_flags("tenant-123")

            assert result == {}

    @pytest.mark.asyncio
    async def test_get_all_flags_returns_empty_when_tenant_none(self):
        """Test get_all_flags returns empty dict when tenant not found."""
        service = FeatureFlagsService()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service.get_all_flags("nonexistent")

            assert result == {}


class TestLoadTenantFlags:
    """Test _load_tenant_flags method."""

    @pytest.mark.asyncio
    async def test_load_tenant_flags_returns_flags(self):
        """Test _load_tenant_flags loads flags from tenant."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = {"loaded": True}

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service._load_tenant_flags("tenant-123")

            assert result == {"loaded": True}

    @pytest.mark.asyncio
    async def test_load_tenant_flags_returns_empty_when_not_found(self):
        """Test _load_tenant_flags returns empty dict when tenant not found."""
        service = FeatureFlagsService()

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service._load_tenant_flags("nonexistent")

            assert result == {}

    @pytest.mark.asyncio
    async def test_load_tenant_flags_handles_none_feature_flags(self):
        """Test _load_tenant_flags handles None feature_flags."""
        service = FeatureFlagsService()

        mock_tenant = MagicMock()
        mock_tenant.feature_flags = None

        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tenant
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch.object(service, "_session_maker", return_value=mock_session):
            result = await service._load_tenant_flags("tenant-123")

            # Should return empty dict when feature_flags is None
            assert result == {}


class TestSingletonInstance:
    """Test singleton instance."""

    def test_singleton_exists(self):
        """Test that feature_flags_service singleton exists."""
        from src.app.feature_flags import feature_flags_service

        assert feature_flags_service is not None
        assert isinstance(feature_flags_service, FeatureFlagsService)
