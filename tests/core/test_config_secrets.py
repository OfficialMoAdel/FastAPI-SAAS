import pytest
import logging
from pydantic import SecretStr, ValidationError
from src.app.core.config import CryptSettings


def test_encryption_keys_missing_warning(caplog):
    """Verify warning is logged when encryption keys are missing."""
    caplog.set_level(logging.WARNING)

    # Create settings with valid SECRET_KEY but missing encryption keys
    # Note: We provide a valid SECRET_KEY to satisfy the basic validation
    CryptSettings(SECRET_KEY=SecretStr("s" * 32), ENCRYPTION_KEY=None, ENCRYPTION_SALT=None)

    assert "SECURITY: Encryption keys (ENCRYPTION_KEY, ENCRYPTION_SALT) are missing" in caplog.text


def test_encryption_keys_present_no_warning(caplog):
    """Verify no warning is logged when encryption keys are present."""
    caplog.set_level(logging.WARNING)

    CryptSettings(
        SECRET_KEY=SecretStr("s" * 32), ENCRYPTION_KEY=SecretStr("k" * 32), ENCRYPTION_SALT=SecretStr("s" * 16)
    )

    assert "SECURITY: Encryption keys (ENCRYPTION_KEY, ENCRYPTION_SALT) are missing" not in caplog.text
