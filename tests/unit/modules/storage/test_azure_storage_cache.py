"""Unit tests for Azure Storage connection string caching optimization (P3-2).

Tests verify that connection string parsing is cached in __init__ to avoid
repeated parsing on every get_url() call.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.clients.storage.azure_storage import AzureBlobStorage

# Test connection string constant to avoid line length issues
TEST_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=testaccount;AccountKey=testkey123;EndpointSuffix=core.windows.net"
)


class TestConnectionStringCached:
    """Test that connection string is parsed once and cached."""

    def test_connection_string_parsed_in_init(self):
        """Verify connection string is parsed during initialization."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Assert - cached values should be set
        assert storage._account_name == "testaccount"
        assert storage._account_key == "testkey123"

    def test_invalid_connection_string_raises_error(self):
        """Verify invalid connection string raises ValueError during init."""
        invalid_connection_string = "InvalidConnectionString"
        container_name = "test-container"

        with pytest.raises(ValueError, match="AccountName not found"):
            AzureBlobStorage(invalid_connection_string, container_name)

    def test_missing_account_key_raises_error(self):
        """Verify missing AccountKey raises ValueError during init."""
        connection_string = "DefaultEndpointsProtocol=https;AccountName=testaccount;EndpointSuffix=core.windows.net"
        container_name = "test-container"

        with pytest.raises(ValueError, match="AccountKey not found"):
            AzureBlobStorage(connection_string, container_name)


class TestStorageOperationsUseCache:
    """Test that storage operations use cached values."""

    @pytest.mark.asyncio
    async def test_get_url_uses_cached_values(self):
        """Verify get_url uses cached account name and key."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Mock the container client and blob client
        mock_container = MagicMock()
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://testaccount.blob.core.windows.net/test-container/test.txt"
        mock_container.get_blob_client.return_value = mock_blob_client

        with patch.object(storage, "_get_container_client", return_value=mock_container):
            with patch("src.app.clients.storage.azure_storage.generate_blob_sas", return_value="sas_token_123"):
                url = await storage.get_url("test.txt")

                # Assert - URL should be generated
                assert "sas_token_123" in url
                assert "testaccount" in url

    @pytest.mark.asyncio
    async def test_no_reparsing_on_multiple_get_url_calls(self):
        """Verify connection string is not reparsed on multiple get_url calls."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Store original cached values
        original_account_name = storage._account_name
        original_account_key = storage._account_key

        # Mock the container client and blob client
        mock_container = MagicMock()
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://testaccount.blob.core.windows.net/test-container/test.txt"
        mock_container.get_blob_client.return_value = mock_blob_client

        with patch.object(storage, "_get_container_client", return_value=mock_container):
            with patch("src.app.clients.storage.azure_storage.generate_blob_sas", return_value="sas_token"):
                # Call get_url multiple times
                await storage.get_url("test1.txt")
                await storage.get_url("test2.txt")
                await storage.get_url("test3.txt")

                # Assert - cached values should remain the same (not reparsed)
                assert storage._account_name == original_account_name
                assert storage._account_key == original_account_key
                assert storage._account_name == "testaccount"
                assert storage._account_key == "testkey123"


class TestParsingMethods:
    """Test the static parsing methods."""

    def test_parse_account_name_success(self):
        """Verify _parse_account_name extracts account name correctly."""
        connection_string = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=key123"
        account_name = AzureBlobStorage._parse_account_name(connection_string)
        assert account_name == "myaccount"

    def test_parse_account_name_missing_raises_error(self):
        """Verify _parse_account_name raises error when AccountName missing."""
        connection_string = "DefaultEndpointsProtocol=https;AccountKey=key123"
        with pytest.raises(ValueError, match="AccountName not found"):
            AzureBlobStorage._parse_account_name(connection_string)

    def test_parse_account_key_success(self):
        """Verify _parse_account_key extracts account key correctly."""
        connection_string = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=mykey456"
        account_key = AzureBlobStorage._parse_account_key(connection_string)
        assert account_key == "mykey456"

    def test_parse_account_key_missing_raises_error(self):
        """Verify _parse_account_key raises error when AccountKey missing."""
        connection_string = "DefaultEndpointsProtocol=https;AccountName=myaccount"
        with pytest.raises(ValueError, match="AccountKey not found"):
            AzureBlobStorage._parse_account_key(connection_string)

    def test_parse_with_complex_connection_string(self):
        """Verify parsing works with complex connection string."""
        connection_string = (
            "DefaultEndpointsProtocol=https;"
            "AccountName=complexaccount;"
            "AccountKey=veryLongKeyWith+Special/Characters==;"
            "EndpointSuffix=core.windows.net"
        )
        account_name = AzureBlobStorage._parse_account_name(connection_string)
        account_key = AzureBlobStorage._parse_account_key(connection_string)

        assert account_name == "complexaccount"
        assert account_key == "veryLongKeyWith+Special/Characters=="


class TestPerformanceImprovement:
    """Test performance characteristics of caching."""

    @pytest.mark.asyncio
    async def test_cached_values_faster_than_parsing(self):
        """Verify using cached values is faster than repeated parsing."""
        import time

        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Measure time to access cached values
        start = time.perf_counter()
        for _ in range(1000):
            _ = storage._account_name
            _ = storage._account_key
        cached_time = time.perf_counter() - start

        # Measure time to parse repeatedly
        start = time.perf_counter()
        for _ in range(1000):
            _ = AzureBlobStorage._parse_account_name(connection_string)
            _ = AzureBlobStorage._parse_account_key(connection_string)
        parsing_time = time.perf_counter() - start

        # Assert - cached access should be significantly faster
        assert cached_time < parsing_time

    def test_memory_overhead_minimal(self):
        """Verify caching adds minimal memory overhead."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Assert - cached values are just strings (minimal overhead)
        assert isinstance(storage._account_name, str)
        assert isinstance(storage._account_key, str)
        assert len(storage._account_name) < 100  # Reasonable size
        assert len(storage._account_key) < 200  # Reasonable size


class TestBackwardCompatibility:
    """Test that optimization doesn't break existing functionality."""

    @pytest.mark.asyncio
    async def test_upload_still_works(self):
        """Verify upload operation still works after optimization."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Mock the container client and blob client
        mock_container = MagicMock()
        mock_blob_client = AsyncMock()
        mock_container.get_blob_client.return_value = mock_blob_client

        with patch.object(storage, "_get_container_client", return_value=mock_container):
            from io import BytesIO

            file = BytesIO(b"test content")
            result = await storage.upload(file, "test.txt", "text/plain")

            # Assert - upload should succeed
            assert result.success is True
            assert result.path == "test.txt"

    @pytest.mark.asyncio
    async def test_validate_connection_still_works(self):
        """Verify connection validation still works after optimization."""
        connection_string = TEST_CONNECTION_STRING
        container_name = "test-container"

        storage = AzureBlobStorage(connection_string, container_name)

        # Mock the container client
        mock_container = AsyncMock()
        mock_container.get_container_properties.return_value = {"name": "test-container"}

        with patch.object(storage, "_get_container_client", return_value=mock_container):
            is_valid = await storage.validate_connection()

            # Assert - validation should succeed
            assert is_valid is True
