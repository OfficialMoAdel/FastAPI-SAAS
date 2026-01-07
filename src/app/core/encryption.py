"""Encryption utilities for sensitive data.

This module provides Fernet-based encryption/decryption for sensitive data
like storage credentials, supporting key rotation for security.

Usage:
    from src.app.core.encryption import encrypt_value, decrypt_value

    encrypted = encrypt_value("my_secret", encryption_key)
    decrypted = decrypt_value(encrypted, encryption_key)
"""

from __future__ import annotations

import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """Get the primary encryption key from environment.

    Returns:
        Encryption key as bytes.

    Raises:
        ValueError: If ENCRYPTION_KEY is not set.
    """
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY environment variable is not set")
    return key.encode() if isinstance(key, str) else key


def get_old_encryption_key() -> bytes | None:
    """Get the old encryption key for rotation support.

    Returns:
        Old encryption key as bytes, or None if not set.
    """
    key = os.getenv("ENCRYPTION_KEY_OLD")
    if not key:
        return None
    return key.encode() if isinstance(key, str) else key


def generate_encryption_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        Base64-encoded Fernet key.
    """
    return Fernet.generate_key().decode()


def encrypt_value(value: str, key: bytes | None = None) -> bytes:
    """Encrypt a string value using Fernet.

    Args:
        value: String value to encrypt.
        key: Optional encryption key. Uses ENCRYPTION_KEY if not provided.

    Returns:
        Encrypted value as bytes (suitable for BYTEA column).
    """
    if key is None:
        key = get_encryption_key()

    fernet = Fernet(key)
    return fernet.encrypt(value.encode())


def decrypt_value(encrypted: bytes, key: bytes | None = None) -> str:
    """Decrypt a value using Fernet with key rotation support.

    Attempts decryption with the primary key first, then falls back
    to the old key if available. This enables gradual key rotation.

    Args:
        encrypted: Encrypted value as bytes.
        key: Optional primary encryption key. Uses ENCRYPTION_KEY if not provided.

    Returns:
        Decrypted string value.

    Raises:
        InvalidToken: If decryption fails with all available keys.
    """
    if key is None:
        key = get_encryption_key()

    # Try primary key first
    try:
        fernet = Fernet(key)
        return fernet.decrypt(encrypted).decode()
    except InvalidToken:
        pass

    # Try old key if available (for rotation)
    old_key = get_old_encryption_key()
    if old_key:
        try:
            fernet = Fernet(old_key)
            result = fernet.decrypt(encrypted).decode()
            logger.info("Decrypted value using old key - should re-encrypt with new key")
            return result
        except InvalidToken:
            pass

    # Both keys failed
    raise InvalidToken("Failed to decrypt with available keys")


def rotate_encryption(encrypted: bytes, old_key: bytes, new_key: bytes) -> bytes:
    """Rotate encryption from old key to new key.

    Args:
        encrypted: Value encrypted with old key.
        old_key: The old encryption key.
        new_key: The new encryption key.

    Returns:
        Value encrypted with new key.
    """
    # Decrypt with old key
    old_fernet = Fernet(old_key)
    decrypted = old_fernet.decrypt(encrypted)

    # Encrypt with new key
    new_fernet = Fernet(new_key)
    return new_fernet.encrypt(decrypted)


def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """Mask a sensitive value for display, showing only last N characters.

    Args:
        value: The sensitive value to mask.
        visible_chars: Number of characters to show at the end (default: 4).

    Returns:
        Masked string like "****abcd".
    """
    if len(value) <= visible_chars:
        return "*" * len(value)
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


__all__ = [
    "get_encryption_key",
    "get_old_encryption_key",
    "generate_encryption_key",
    "encrypt_value",
    "decrypt_value",
    "rotate_encryption",
    "mask_sensitive_value",
]
