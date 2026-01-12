"""Credential encryption utilities for sensitive data storage.

This module provides encryption for passkey credentials, OAuth tokens,
and other sensitive data that must be encrypted at rest.

Features:
- Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
- Key versioning with v{N}: prefix for audit trails
- Key rotation support via MultiFernet
- Dependency injection support via get_encryption()
- Audit logging for security operations

Usage:
    # Via dependency injection (recommended)
    from src.app.core.security.encryption import get_encryption

    @router.post("/credential")
    async def store_credential(
        data: str,
        encryption: CredentialEncryption = Depends(get_encryption)
    ):
        encrypted = encryption.encrypt_column(data)
        ...

    # Direct instantiation (for testing)
    encryption = CredentialEncryption(key=custom_key)
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.app.core.config import EnvironmentOption, settings

logger = logging.getLogger(__name__)

# Current encryption version
CURRENT_VERSION = 1
VERSION_PREFIX = b"v1:"

# SECURITY: Generate a random fallback salt per process if not configured
# This ensures dev environments work but production should always set ENCRYPTION_SALT
_FALLBACK_SALT: bytes | None = None


class EncryptionError(Exception):
    """Raised when encryption fails."""

    def __init__(self, message: str, original_error: Exception | None = None):
        self.message = message
        self.original_error = original_error
        super().__init__(self.message)


class DecryptionError(Exception):
    """Raised when decryption fails (tampered data, wrong key, etc.)."""

    def __init__(self, message: str, original_error: Exception | None = None):
        self.message = message
        self.original_error = original_error
        super().__init__(self.message)


def _get_salt() -> bytes:
    """Get encryption salt from config or generate secure fallback.

    Raises ValueError in production if ENCRYPTION_SALT is not configured.
    """
    global _FALLBACK_SALT

    if settings.ENCRYPTION_SALT:
        return settings.ENCRYPTION_SALT.get_secret_value().encode()

    # SECURITY: Fail fast in production if salt not configured
    if settings.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise ValueError("ENCRYPTION_SALT must be set in production. Generate with: openssl rand -hex 32")

    # Fallback: generate random salt per process (dev/staging only)
    if _FALLBACK_SALT is None:
        _FALLBACK_SALT = os.urandom(32)
        logger.warning(
            "ENCRYPTION_SALT not configured. Using random per-process salt (dev only). "
            "Encrypted data will be unrecoverable after restart!"
        )
    return _FALLBACK_SALT


def _derive_key(secret: str, salt: bytes) -> bytes:
    """Derive a Fernet key from a secret string."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def _get_key_from_settings() -> bytes:
    """Get or derive Fernet key from settings."""
    key_str = settings.ENCRYPTION_KEY.get_secret_value() if settings.ENCRYPTION_KEY else None

    if not key_str:
        # Fallback: derive from SECRET_KEY (not recommended for prod rotation but safe functionality)
        return _derive_key(settings.SECRET_KEY.get_secret_value(), _get_salt())

    # Ensure it's bytes
    try:
        return key_str.encode() if isinstance(key_str, str) else key_str
    except Exception as err:
        raise ValueError("Invalid ENCRYPTION_KEY format.") from err


def _get_previous_key_from_settings() -> bytes | None:
    """Get previous key from settings for rotation support."""
    prev_key_str = getattr(settings, "ENCRYPTION_KEY_PREVIOUS", None)
    if prev_key_str and hasattr(prev_key_str, "get_secret_value"):
        prev_key_value = prev_key_str.get_secret_value()
        if prev_key_value:
            try:
                return prev_key_value.encode() if isinstance(prev_key_value, str) else prev_key_value
            except Exception as e:
                logger.warning(f"Invalid ENCRYPTION_KEY_PREVIOUS format: {e}")
    return None


class CredentialEncryption:
    """Encryption service for sensitive credential data.

    Uses Fernet symmetric encryption with AES-128-CBC and HMAC-SHA256
    for authenticated encryption. Each encryption includes a unique IV
    to ensure identical plaintexts produce different ciphertexts.

    Features:
    - Key versioning: All encrypted data is prefixed with version (v1:)
    - Key rotation: Supports decrypting with previous key via MultiFernet
    - Audit logging: All operations are logged for security auditing

    Example:
        encryption = CredentialEncryption()
        encrypted = encryption.encrypt_credential(b"secret")
        decrypted = encryption.decrypt_credential(encrypted)
    """

    def __init__(
        self,
        key: bytes | None = None,
        previous_key: bytes | None = None,
    ):
        """Initialize encryption with Fernet key(s).

        Args:
            key: Optional Fernet key. If not provided, loads from settings.
            previous_key: Optional previous key for rotation support.
        """
        if key is None:
            key = _get_key_from_settings()
        if previous_key is None:
            previous_key = _get_previous_key_from_settings()

        self._key = key
        self._fernet = Fernet(key)

        # Setup MultiFernet for key rotation support
        fernets = [self._fernet]
        if previous_key:
            try:
                fernets.append(Fernet(previous_key))
                logger.info("Key rotation active: using previous key as fallback for decryption")
            except Exception as e:
                logger.warning(f"Failed to initialize previous key: {e}")

        self._multi_fernet = MultiFernet(fernets)

    def encrypt_credential(self, credential_data: bytes) -> bytes:
        """Encrypt credential data with version prefix.

        Args:
            credential_data: Raw credential bytes to encrypt.

        Returns:
            Encrypted credential data with version prefix (v1:...).

        Raises:
            EncryptionError: If encryption fails.
        """
        try:
            encrypted = self._fernet.encrypt(credential_data)
            versioned = VERSION_PREFIX + encrypted

            logger.debug(
                "Encrypted credential data",
                extra={
                    "operation": "encrypt",
                    "version": CURRENT_VERSION,
                    "data_size": len(credential_data),
                },
            )

            return versioned
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise EncryptionError("Failed to encrypt credential data", e) from e

    def decrypt_credential(self, encrypted_data: bytes) -> bytes:
        """Decrypt credential data.

        Handles both versioned (v1:...) and legacy (unversioned) data.

        Args:
            encrypted_data: Encrypted credential bytes.

        Returns:
            Decrypted credential data.

        Raises:
            DecryptionError: If the data is tampered or from a different key.
        """
        try:
            # Check for version prefix
            if encrypted_data.startswith(b"v") and b":" in encrypted_data[:5]:
                # Parse version
                prefix_end = encrypted_data.index(b":") + 1
                version = int(encrypted_data[1 : prefix_end - 1])
                actual_encrypted = encrypted_data[prefix_end:]

                logger.debug(
                    "Decrypting versioned data",
                    extra={"operation": "decrypt", "version": version},
                )
            else:
                # Legacy unversioned data
                actual_encrypted = encrypted_data
                logger.debug(
                    "Decrypting legacy unversioned data",
                    extra={"operation": "decrypt", "version": 0},
                )

            # Use MultiFernet for rotation support
            decrypted = self._multi_fernet.decrypt(actual_encrypted)

            logger.debug(
                "Decrypted credential data",
                extra={"operation": "decrypt", "data_size": len(decrypted)},
            )

            return decrypted
        except InvalidToken as e:
            logger.warning("Decryption failed: invalid token (tampered or wrong key)")
            raise DecryptionError(
                "Failed to decrypt: data may be tampered or encrypted with different key",
                e,
            ) from e
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise DecryptionError("Failed to decrypt credential data", e) from e

    def encrypt_column(self, value: str) -> str:
        """Encrypt a string value for database storage.

        Args:
            value: String value to encrypt.

        Returns:
            Base64-encoded encrypted value with version prefix.
        """
        encrypted_bytes = self.encrypt_credential(value.encode())
        return encrypted_bytes.decode()

    def decrypt_column(self, encrypted_value: str) -> str:
        """Decrypt a database column value.

        Args:
            encrypted_value: Encrypted string value.

        Returns:
            Decrypted string value.

        Raises:
            DecryptionError: If the data is tampered or from a different key.
        """
        decrypted_bytes = self.decrypt_credential(encrypted_value.encode())
        return decrypted_bytes.decode()

    def encrypt_dict(self, data: dict[str, Any]) -> dict[str, str]:
        """Encrypt dictionary values for storage.

        Args:
            data: Dictionary with string values to encrypt.

        Returns:
            Dictionary with encrypted string values.
        """
        return {k: self.encrypt_column(str(v)) for k, v in data.items()}

    def decrypt_dict(self, encrypted_data: dict[str, str]) -> dict[str, str]:
        """Decrypt dictionary values from storage.

        Args:
            encrypted_data: Dictionary with encrypted values.

        Returns:
            Dictionary with decrypted string values.
        """
        return {k: self.decrypt_column(v) for k, v in encrypted_data.items()}

    def rotate(self, encrypted_data: bytes) -> bytes:
        """Re-encrypt data with the current key.

        Use this to migrate data from old key to new key during rotation.

        Args:
            encrypted_data: Data encrypted with any valid key.

        Returns:
            Data re-encrypted with the current (primary) key and new version.
        """
        # Decrypt with any valid key, re-encrypt with current key
        decrypted = self.decrypt_credential(encrypted_data)
        return self.encrypt_credential(decrypted)


# Cached singleton for dependency injection
@lru_cache(maxsize=1)
def get_encryption() -> CredentialEncryption:
    """Get CredentialEncryption instance for dependency injection.

    Returns:
        Cached CredentialEncryption instance.

    Example:
        @router.post("/store")
        async def store_data(
            data: str,
            encryption: CredentialEncryption = Depends(get_encryption)
        ):
            encrypted = encryption.encrypt_column(data)
    """
    return CredentialEncryption()


def generate_fernet_key() -> bytes:
    """Generate a new Fernet encryption key.

    Returns:
        32-byte random key suitable for Fernet encryption.
    """
    return Fernet.generate_key()


# ============================================================================
# Backward-compatible functions (for existing code)
# ============================================================================


def _get_fernet() -> Fernet:
    """Get Fernet instance for the current encryption key.

    DEPRECATED: Use CredentialEncryption class instead.
    """
    return Fernet(_get_key_from_settings())


def _get_multi_fernet() -> MultiFernet:
    """Get MultiFernet supporting current and previous keys for rotation.

    DEPRECATED: Use CredentialEncryption class instead.
    """
    current_fernet = _get_fernet()
    fernets = [current_fernet]

    prev_key = _get_previous_key_from_settings()
    if prev_key:
        try:
            fernets.append(Fernet(prev_key))
            logger.info("Key rotation active: using previous key as fallback")
        except Exception as e:
            logger.warning(f"Invalid ENCRYPTION_KEY_PREVIOUS format: {e}")

    return MultiFernet(fernets)


def encrypt_data(data: str) -> str:
    """Encrypt a string using the current key.

    DEPRECATED: Use CredentialEncryption.encrypt_column() instead.
    """
    encryption = get_encryption()
    return encryption.encrypt_column(data)


def decrypt_data(data: str) -> str:
    """Decrypt a string, trying current key first then previous key.

    DEPRECATED: Use CredentialEncryption.decrypt_column() instead.
    """
    encryption = get_encryption()
    return encryption.decrypt_column(data)


def rotate_encryption(encrypted_data: str) -> str:
    """Re-encrypt data with the current key.

    DEPRECATED: Use CredentialEncryption.rotate() instead.

    Args:
        encrypted_data: Data encrypted with any valid key.

    Returns:
        Data re-encrypted with the current (primary) key.
    """
    encryption = get_encryption()
    rotated_bytes = encryption.rotate(encrypted_data.encode())
    return rotated_bytes.decode()
