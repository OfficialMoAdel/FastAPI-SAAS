"""Unit tests for JWT PEM key pre-parsing optimization (z28g.4).

Tests the lru_cache-based PEM key caching for RS256 deployments.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

logger = logging.getLogger(__name__)


class TestPEMKeyCaching:
    """Test that PEM keys are cached after first parse."""

    def test_pem_caching_rs256(self):
        """Verify pre-parsed key is cached for RS256."""
        # First, clear any existing cache
        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        clear_jwt_key_cache()

        # Mock RS256 settings
        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = "dummy_private_key"
        mock_settings.JWT_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/ygWyf8Adw5gKOLxMBZ
-----END PUBLIC KEY-----"""
        mock_settings.SECRET_KEY = MagicMock()
        mock_settings.SECRET_KEY.get_secret_value.return_value = "test_secret"

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            with patch("src.app.core.security.jwt_utils.load_pem_public_key") as mock_load:
                mock_key = MagicMock()
                mock_key.__class__.__name__ = "RSAPublicKey"
                mock_load.return_value = mock_key

                # Clear cache again after patching
                clear_jwt_key_cache()

                # Call twice
                key1 = _get_parsed_verification_key()
                key2 = _get_parsed_verification_key()

                # Verify load_pem_public_key called only ONCE (cached)
                logger.info(f"load_pem_public_key call count: {mock_load.call_count}")
                assert mock_load.call_count == 1

                # Verify same object returned
                assert key1 is key2

    def test_hs256_returns_string(self):
        """Verify HS256 path returns secret string directly."""
        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        clear_jwt_key_cache()

        # Mock HS256 settings (no private key)
        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = None
        mock_settings.SECRET_KEY = MagicMock()
        mock_settings.SECRET_KEY.get_secret_value.return_value = "my_secret_key"

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            # Clear cache after patching
            clear_jwt_key_cache()

            key = _get_parsed_verification_key()

            # Verify string returned (not parsed key object)
            logger.info(f"HS256 key type: {type(key)}")
            assert isinstance(key, str)
            assert key == "my_secret_key"


class TestCacheClearUtility:
    """Test the cache clearing utility."""

    def test_clear_cache_resets_lru_cache(self):
        """Verify clear_jwt_key_cache() resets the cache."""
        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        # Mock settings
        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = None
        mock_settings.SECRET_KEY = MagicMock()
        mock_settings.SECRET_KEY.get_secret_value.return_value = "secret1"

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            clear_jwt_key_cache()
            _get_parsed_verification_key()

            # Change secret
            mock_settings.SECRET_KEY.get_secret_value.return_value = "secret2"

            # Without clearing - should still return cached value
            key2 = _get_parsed_verification_key()
            assert key2 == "secret1"  # Still cached

            # Clear cache
            clear_jwt_key_cache()

            # Now should return new value
            key3 = _get_parsed_verification_key()
            logger.info(f"After clear: key3={key3}")
            assert key3 == "secret2"


class TestDecodeWithParsedKey:
    """Test that decode_jwt_token uses the cached parsed key."""

    @pytest.mark.asyncio
    async def test_decode_uses_parsed_key(self):
        """Verify decode_jwt_token calls _get_parsed_verification_key."""
        from src.app.core.security.jwt_utils import decode_jwt_token

        with patch("src.app.core.security.jwt_utils._get_parsed_verification_key") as mock_get_key:
            with patch("src.app.core.security.jwt_utils._get_algorithm") as mock_algo:
                mock_get_key.return_value = "test_secret"
                mock_algo.return_value = "HS256"

                with patch("src.app.core.security.jwt_utils.jwt.decode") as mock_decode:
                    mock_decode.return_value = {"sub": "user1"}

                    await decode_jwt_token("dummy.token.here")

                    # Verify _get_parsed_verification_key was called
                    mock_get_key.assert_called_once()

                    # Verify jwt.decode received the parsed key
                    call_args = mock_decode.call_args
                    logger.info(f"jwt.decode called with key: {call_args[0][1]}")
                    assert call_args[0][1] == "test_secret"


class TestEmptyKeyHandling:
    """Test edge cases with missing/empty keys."""

    def test_empty_public_key_returns_empty_string(self):
        """Verify empty public key returns empty string (fails validation later)."""
        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = "some_private_key"
        mock_settings.JWT_PUBLIC_KEY = ""  # Empty
        mock_settings.SECRET_KEY = MagicMock()

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            clear_jwt_key_cache()
            key = _get_parsed_verification_key()

            logger.info(f"Empty public key result: {repr(key)}")
            assert key == ""

    def test_none_public_key_returns_empty_string(self):
        """Verify None public key returns empty string."""
        from src.app.core.security.jwt_utils import (
            _get_parsed_verification_key,
            clear_jwt_key_cache,
        )

        mock_settings = MagicMock()
        mock_settings.JWT_PRIVATE_KEY = "some_private_key"
        mock_settings.JWT_PUBLIC_KEY = None  # None
        mock_settings.SECRET_KEY = MagicMock()

        with patch("src.app.core.security.jwt_utils.settings", mock_settings):
            clear_jwt_key_cache()
            key = _get_parsed_verification_key()

            logger.info(f"None public key result: {repr(key)}")
            assert key == ""
