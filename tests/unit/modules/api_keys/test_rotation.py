"""Unit tests for API key rotation functionality.

Tests cover:
- valid_from timestamp validation (grace period rotation)
- rotate_api_key service method
- Zero-downtime key rotation workflow
- Permission checks and security
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.api_keys.models import APIKey
from src.app.modules.api_keys.service import APIKeyService
from src.app.shared.exceptions import ForbiddenError as ForbiddenException
from src.app.shared.exceptions import ModelNotFoundError


class TestAPIKeyValidFromValidation:
    """Tests for valid_from timestamp validation."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> APIKeyService:
        """Provide an APIKeyService instance with mocked dependencies."""
        return APIKeyService(db=mock_db_session)

    @pytest.mark.asyncio
    async def test_key_valid_when_no_valid_from(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that key is valid when valid_from is None (default behavior)."""

        # Mock the query result with no valid_from
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.valid_from = None  # No valid_from set
        mock_key.expires_at = None
        mock_key.scopes = None

        # Mock the result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        mock_db_session.execute.return_value = mock_result

        # Mock crud_api_keys.update
        with patch.object(api_key_service.crud, "update") as mock_update:
            mock_update.return_value = None

            result = await api_key_service._validate_from_db("hashed_key_123")

            assert result is not None
            assert result["key_id"] == 1

    @pytest.mark.asyncio
    async def test_key_valid_when_valid_from_in_past(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that key is valid when valid_from is in the past."""

        # Mock the query result with past valid_from
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.valid_from = datetime.now(UTC) - timedelta(days=1)  # 1 day ago
        mock_key.expires_at = None
        mock_key.scopes = None

        # Mock the result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        mock_db_session.execute.return_value = mock_result

        # Mock crud_api_keys.update
        with patch.object(api_key_service.crud, "update") as mock_update:
            mock_update.return_value = None

            result = await api_key_service._validate_from_db("hashed_key_123")

            assert result is not None
            assert result["key_id"] == 1

    @pytest.mark.asyncio
    async def test_key_invalid_when_valid_from_in_future(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that key is invalid when valid_from is in the future (grace period)."""

        # Mock the query result with future valid_from
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.valid_from = datetime.now(UTC) + timedelta(days=3)  # 3 days in future
        mock_key.expires_at = None
        mock_key.scopes = None

        # Mock the result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_key
        mock_db_session.execute.return_value = mock_result

        result = await api_key_service._validate_from_db("hashed_key_123")

        # Key should be invalid because valid_from is in the future
        assert result is None


class TestAPIKeyRotation:
    """Tests for rotate_api_key service method."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        mock = AsyncMock(spec=AsyncSession)

        async def side_effect_refresh(obj, *args, **kwargs):
            obj.id = 456
            return None

        mock.refresh.side_effect = side_effect_refresh
        return mock

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> APIKeyService:
        """Provide an APIKeyService instance with mocked dependencies."""
        return APIKeyService(db=mock_db_session)

    @pytest.fixture
    def existing_key_data(self) -> dict:
        """Provide data for an existing API key."""
        return {
            "id": 1,
            "name": "Production Key",
            "user_id": 1,
            "tenant_id": 1,
            "is_active": True,
            "scopes": None,
            "expires_at": None,
        }

    @pytest.mark.asyncio
    async def test_rotate_key_success(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock, existing_key_data: dict
    ) -> None:
        """Test successful key rotation with grace period."""

        # Mock the existing key
        mock_key = MagicMock(spec=APIKey)
        for key, value in existing_key_data.items():
            setattr(mock_key, key, value)

        # Mock crud_api_keys.get
        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish", new_callable=AsyncMock),
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_update.return_value = None

            # Mock _generate_key
            with patch.object(api_key_service, "_generate_key") as mock_gen:
                mock_gen.return_value = ("test_full_key_abc123", "abc123", "hashed_test_key")

                # Perform rotation
                result = await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,
                    grace_period_days=7,
                )

                # Verify result
                assert result is not None
                assert result.key == "test_full_key_abc123"
                assert result.key_prefix == "abc123"
                assert "Rotated" in result.name

                # Verify database operations
                assert mock_db_session.add.called
                assert mock_db_session.commit.called
                assert mock_db_session.refresh.called

    @pytest.mark.asyncio
    async def test_rotate_key_valid_from_set_correctly(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock, existing_key_data: dict
    ) -> None:
        """Test that the new key is immediately valid (valid_from=None) and the old key
        gets its expiry set to now + grace_period via crud_api_keys.update."""
        from datetime import timedelta

        # Mock the existing key
        mock_key = MagicMock(spec=APIKey)
        for key, value in existing_key_data.items():
            setattr(mock_key, key, value)

        before_rotation = datetime.now(UTC)

        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish", new_callable=AsyncMock),
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_update.return_value = None

            with patch.object(api_key_service, "_generate_key") as mock_gen:
                mock_gen.return_value = ("test_key", "prefix", "hashed")

                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,
                    grace_period_days=7,
                )

                after_rotation = datetime.now(UTC)

                # BUG FIX VERIFICATION: New key should be immediately valid (valid_from=None)
                new_key_record = mock_db_session.add.call_args_list[0][0][0]
                assert new_key_record.valid_from is None, (
                    "New key must be immediately valid (valid_from=None). "
                    "Grace period applies to the OLD key's expires_at, not the new key's valid_from."
                )

                # Verify the OLD key was updated with expires_at = now + grace_period
                mock_update.assert_called_once()
                update_kwargs = mock_update.call_args[1]
                old_key_expires_at = update_kwargs["object"]["expires_at"]
                expected_min = before_rotation + timedelta(days=6, hours=23, minutes=59)
                expected_max = after_rotation + timedelta(days=7, hours=0, minutes=1)
                assert expected_min <= old_key_expires_at <= expected_max, (
                    f"Old key expires_at={old_key_expires_at} should be ~7 days from now"
                )

    @pytest.mark.asyncio
    async def test_rotate_key_scopes_copied(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that scopes are copied from old key to new key."""
        scopes = '["read:users", "write:posts"]'

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.name = "Test Key"
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.scopes = scopes
        mock_key.expires_at = None

        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish", new_callable=AsyncMock),
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_update.return_value = None

            with patch.object(api_key_service, "_generate_key") as mock_gen:
                mock_gen.return_value = ("test_key", "prefix", "hashed")

                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,
                )

                # Verify scopes were copied
                call_args = mock_db_session.add.call_args_list[0][0][0]
                assert call_args.scopes == scopes

    @pytest.mark.asyncio
    async def test_rotate_key_expiration_copied(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that expiration is copied from old key to new key."""
        expires = datetime.now(UTC) + timedelta(days=30)

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.name = "Test Key"
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.scopes = None
        mock_key.expires_at = expires

        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update", new_callable=AsyncMock) as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish", new_callable=AsyncMock),
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_update.return_value = None

            with patch.object(api_key_service, "_generate_key") as mock_gen:
                mock_gen.return_value = ("test_key", "prefix", "hashed")

                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,
                )

                # Verify expiration was copied
                call_args = mock_db_session.add.call_args_list[0][0][0]
                assert call_args.expires_at == expires

    @pytest.mark.asyncio
    async def test_rotate_key_wrong_user(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that rotation fails if user doesn't own the key.

        Item 8: the scoped lookup (tenant + user filter) finds no row, so
        cross-user keys are indistinguishable from missing (404, no
        existence oracle) — the dead explicit ownership check is gone.
        """
        with patch.object(api_key_service.crud, "get") as mock_get:
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = None

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = None

            with pytest.raises(ModelNotFoundError):
                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,  # Requesting user
                    tenant_id=1,
                )

    @pytest.mark.asyncio
    async def test_rotate_key_wrong_tenant(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that rotation fails if tenant doesn't match (404, no oracle)."""
        with patch.object(api_key_service.crud, "get") as mock_get:
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = None

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = None

            with pytest.raises(ModelNotFoundError):
                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,  # Requesting tenant
                )

    @pytest.mark.asyncio
    async def test_rotate_revoked_key_fails(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that rotation fails for already revoked keys."""
        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = False  # Already revoked

        with patch.object(api_key_service.crud, "get") as mock_get:
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key

            with pytest.raises(ForbiddenException) as exc_info:
                await api_key_service.rotate_api_key(
                    key_id=1,
                    user_id=1,
                    tenant_id=1,
                )

            assert "revoked" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_key(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that rotation fails for non-existent keys."""
        with patch.object(api_key_service.crud, "get") as mock_get:
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = None

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = None

            with pytest.raises(ModelNotFoundError) as exc_info:
                await api_key_service.rotate_api_key(
                    key_id=999,
                    user_id=1,
                    tenant_id=1,
                )

            assert "APIKey" in str(exc_info.value)


class TestAPIKeyRotationGracePeriod:
    """Tests for grace period behavior during rotation."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Provide a mock database session."""
        mock = AsyncMock(spec=AsyncSession)

        async def side_effect_refresh(obj, *args, **kwargs):
            obj.id = 456
            return None

        mock.refresh.side_effect = side_effect_refresh
        return mock

    @pytest.fixture
    def api_key_service(self, mock_db_session: AsyncMock) -> APIKeyService:
        """Provide an APIKeyService instance."""
        return APIKeyService(db=mock_db_session)

    @pytest.mark.asyncio
    async def test_default_grace_period_is_7_days(
        self, api_key_service: APIKeyService, mock_db_session: AsyncMock
    ) -> None:
        """Test that default grace period is 7 days."""
        from datetime import timedelta

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.name = "Test"
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.scopes = None
        mock_key.expires_at = None

        before = datetime.now(UTC)

        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update") as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish"),
            patch.object(api_key_service, "_generate_key") as mock_gen,
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_gen.return_value = ("test", "prefix", "hashed")

            await api_key_service.rotate_api_key(
                key_id=1,
                user_id=1,
                tenant_id=1,
            )

            after = datetime.now(UTC)

            # 1. New key should be immediately valid
            call_args = mock_db_session.add.call_args_list[0][0][0]
            assert call_args.valid_from is None

            # 2. Old key should expire after 7 days (default grace period)
            update_call_args = mock_update.call_args[1]["object"]
            expected_min = before + timedelta(days=6, hours=23)
            expected_max = after + timedelta(days=7, hours=1)

            assert "expires_at" in update_call_args
            assert expected_min <= update_call_args["expires_at"] <= expected_max

    @pytest.mark.asyncio
    async def test_custom_grace_period(self, api_key_service: APIKeyService, mock_db_session: AsyncMock) -> None:
        """Test that custom grace period is respected."""
        from datetime import timedelta

        mock_key = MagicMock(spec=APIKey)
        mock_key.id = 1
        mock_key.name = "Test"
        mock_key.user_id = 1
        mock_key.tenant_id = 1
        mock_key.is_active = True
        mock_key.scopes = None
        mock_key.expires_at = None

        custom_days = 14
        before = datetime.now(UTC)

        with (
            patch.object(api_key_service.crud, "get") as mock_get,
            patch.object(api_key_service.crud, "update") as mock_update,
            patch("src.app.modules.api_keys.service.event_bus.publish"),
            patch.object(api_key_service, "_generate_key") as mock_gen,
        ):
            mock_result = MagicMock()

            mock_result.scalar_one_or_none.return_value = mock_key

            api_key_service.db.execute = AsyncMock(return_value=mock_result)

            mock_get.return_value = mock_key
            mock_gen.return_value = ("test_key", "prefix", "hashed")

            await api_key_service.rotate_api_key(
                key_id=1,
                user_id=1,
                tenant_id=1,
                grace_period_days=custom_days,
            )

            after = datetime.now(UTC)

            # 1. New key should be immediately valid
            call_args = mock_db_session.add.call_args_list[0][0][0]
            assert call_args.valid_from is None

            # 2. Old key should expire after custom_days
            update_call_args = mock_update.call_args[1]["object"]
            expected_min = before + timedelta(days=custom_days - 1, hours=23)
            expected_max = after + timedelta(days=custom_days, hours=1)

            assert "expires_at" in update_call_args
            assert expected_min <= update_call_args["expires_at"] <= expected_max
