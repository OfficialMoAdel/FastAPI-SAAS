"""Tests for verifying bcrypt rounds are configurable.

This test suite ensures that:
1. BCRYPT_ROUNDS setting is respected by get_password_hash()
2. Hashes with different rounds are still verifiable
3. Configuration validation works properly

Regression test for P0-2: Make Bcrypt Rounds Configurable
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.app.core.security import get_password_hash, verify_password


class TestBcryptRoundsConfigurable:
    """Verify bcrypt rounds configuration is used correctly."""

    @pytest.mark.asyncio
    async def test_default_rounds_produces_valid_hash(self) -> None:
        """Default 12 rounds should produce a valid, verifiable hash."""
        password = "test_password_123"
        hashed = await get_password_hash(password)

        # Verify bcrypt format: $2b$<rounds>$<salt+hash>
        assert hashed.startswith("$2b$")

        # Verify the password matches
        is_valid = await verify_password(password, hashed)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_bcrypt_rounds_configurable_10(self) -> None:
        """Setting BCRYPT_ROUNDS=10 should produce $2b$10$ prefix."""
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 10
            mock_settings.SECRET_KEY.get_secret_value.return_value = "a" * 32
            mock_settings.ALGORITHM = "HS256"
            mock_settings.JWT_PRIVATE_KEY = None
            mock_settings.JWT_PUBLIC_KEY = None

            password = "test_password_456"
            hashed = await get_password_hash(password)

            # Verify rounds prefix
            assert hashed.startswith("$2b$10$"), f"Expected $2b$10$ prefix, got: {hashed[:10]}"

            # Verify password still validates
            is_valid = await verify_password(password, hashed)
            assert is_valid is True

    @pytest.mark.asyncio
    async def test_bcrypt_rounds_configurable_8_dev(self) -> None:
        """Setting BCRYPT_ROUNDS=8 (dev mode) should produce $2b$08$ prefix."""
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 8
            mock_settings.SECRET_KEY.get_secret_value.return_value = "a" * 32
            mock_settings.ALGORITHM = "HS256"
            mock_settings.JWT_PRIVATE_KEY = None
            mock_settings.JWT_PUBLIC_KEY = None

            password = "dev_test_password"
            hashed = await get_password_hash(password)

            # Verify rounds prefix (08 for single digit)
            assert hashed.startswith("$2b$08$"), f"Expected $2b$08$ prefix, got: {hashed[:10]}"

    @pytest.mark.asyncio
    async def test_hashes_with_different_rounds_verify_correctly(self) -> None:
        """Hashes made with different rounds should all verify with verify_password."""
        password = "cross_verify_test"

        # Create hash with 8 rounds
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 8
            hash_8 = await get_password_hash(password)

        # Create hash with 10 rounds
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 10
            hash_10 = await get_password_hash(password)

        # Both should verify (bcrypt embeds rounds in hash)
        assert await verify_password(password, hash_8) is True
        assert await verify_password(password, hash_10) is True

        # Wrong password should fail
        assert await verify_password("wrong_password", hash_8) is False
        assert await verify_password("wrong_password", hash_10) is False

    @pytest.mark.asyncio
    async def test_higher_rounds_takes_longer(self) -> None:
        """Higher rounds should take measurably longer (security/performance tradeoff)."""
        import time

        password = "timing_test_password"

        # Time 8 rounds
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 8
            start = time.perf_counter()
            await get_password_hash(password)
            time_8 = time.perf_counter() - start

        # Time 10 rounds
        with patch("src.app.core.security.password.settings") as mock_settings:
            mock_settings.BCRYPT_ROUNDS = 10
            start = time.perf_counter()
            await get_password_hash(password)
            time_10 = time.perf_counter() - start

        # 10 rounds should be significantly slower than 8 rounds
        # Each round doubles the work, so 2 extra rounds = 4x slower
        assert time_10 > time_8, f"10 rounds ({time_10:.3f}s) should be slower than 8 rounds ({time_8:.3f}s)"


class TestBcryptRoundsValidation:
    """Test that bcrypt rounds configuration validation works."""

    def test_bcrypt_rounds_default_is_12(self) -> None:
        """Default BCRYPT_ROUNDS should be 12."""
        from src.app.core.config import CryptSettings

        # Create settings with minimal required values
        settings = CryptSettings(SECRET_KEY="a" * 32)  # type: ignore[arg-type]
        assert settings.BCRYPT_ROUNDS == 12

    def test_bcrypt_rounds_accepts_valid_range(self) -> None:
        """BCRYPT_ROUNDS should accept values 8-14."""
        from src.app.core.config import CryptSettings

        for rounds in [8, 10, 12, 14]:
            settings = CryptSettings(SECRET_KEY="a" * 32, BCRYPT_ROUNDS=rounds)  # type: ignore[arg-type]
            assert settings.BCRYPT_ROUNDS == rounds

    def test_bcrypt_rounds_rejects_below_minimum(self) -> None:
        """BCRYPT_ROUNDS < 8 should be rejected."""
        from pydantic import ValidationError

        from src.app.core.config import CryptSettings

        with pytest.raises(ValidationError) as exc_info:
            CryptSettings(SECRET_KEY="a" * 32, BCRYPT_ROUNDS=7)  # type: ignore[arg-type]

        # Check that the error is about BCRYPT_ROUNDS
        assert "BCRYPT_ROUNDS" in str(exc_info.value) or "greater than or equal to 8" in str(exc_info.value)

    def test_bcrypt_rounds_rejects_above_maximum(self) -> None:
        """BCRYPT_ROUNDS > 14 should be rejected."""
        from pydantic import ValidationError

        from src.app.core.config import CryptSettings

        with pytest.raises(ValidationError) as exc_info:
            CryptSettings(SECRET_KEY="a" * 32, BCRYPT_ROUNDS=15)  # type: ignore[arg-type]

        # Check that the error is about BCRYPT_ROUNDS
        assert "BCRYPT_ROUNDS" in str(exc_info.value) or "less than or equal to 14" in str(exc_info.value)
