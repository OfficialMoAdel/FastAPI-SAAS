"""Encryption utilities using cryptography.Fernet.

Supports key rotation: configure ENCRYPTION_KEY_PREVIOUS to enable
decryption with the old key while encrypting with the new key.
"""
import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.app.core.config import EnvironmentOption, settings

logger = logging.getLogger(__name__)

# SECURITY: Generate a random fallback salt per process if not configured
# This ensures dev environments work but production should always set ENCRYPTION_SALT
_FALLBACK_SALT: bytes | None = None


def _get_salt() -> bytes:
    """Get encryption salt from config or generate secure fallback.

    Raises ValueError in production if ENCRYPTION_SALT is not configured.
    """
    global _FALLBACK_SALT

    if settings.ENCRYPTION_SALT:
        return settings.ENCRYPTION_SALT.get_secret_value().encode()

    # SECURITY: Fail fast in production if salt not configured
    if settings.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise ValueError(
            "ENCRYPTION_SALT must be set in production. "
            "Generate with: openssl rand -hex 32"
        )

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


def _get_fernet() -> Fernet:
    """
    Get Fernet instance for the current encryption key.

    If ENCRYPTION_KEY is not set, we generate a deterministic key based on SECRET_KEY
    to prevent runtime errors in dev, but in prod ENCRYPTION_KEY should be set.
    """
    key_str = settings.ENCRYPTION_KEY.get_secret_value() if settings.ENCRYPTION_KEY else None

    if not key_str:
        # Fallback: derive from SECRET_KEY (not recommended for prod rotation but safe functionality)
        key = _derive_key(settings.SECRET_KEY.get_secret_value(), _get_salt())
    else:
        # Ensure it's bytes
        try:
            key = key_str.encode() if isinstance(key_str, str) else key_str
        except Exception:
            raise ValueError("Invalid ENCRYPTION_KEY format.")

    return Fernet(key)


def _get_multi_fernet() -> MultiFernet:
    """Get MultiFernet supporting current and previous keys for rotation.
    
    During key rotation:
    1. Set new key as ENCRYPTION_KEY
    2. Set old key as ENCRYPTION_KEY_PREVIOUS
    3. New data uses new key, old data decrypts with fallback to old key
    4. After re-encrypting all data, remove ENCRYPTION_KEY_PREVIOUS
    """
    current_fernet = _get_fernet()
    fernets = [current_fernet]
    
    # Check for previous key (for rotation)
    prev_key_str = getattr(settings, 'ENCRYPTION_KEY_PREVIOUS', None)
    if prev_key_str and hasattr(prev_key_str, 'get_secret_value'):
        prev_key_value = prev_key_str.get_secret_value()
        if prev_key_value:
            try:
                prev_key = prev_key_value.encode() if isinstance(prev_key_value, str) else prev_key_value
                fernets.append(Fernet(prev_key))
                logger.info("Key rotation active: using previous key as fallback for decryption")
            except Exception as e:
                logger.warning(f"Invalid ENCRYPTION_KEY_PREVIOUS format: {e}")
    
    return MultiFernet(fernets)


def encrypt_data(data: str) -> str:
    """Encrypt a string using the current key."""
    f = _get_fernet()
    return f.encrypt(data.encode()).decode()


def decrypt_data(data: str) -> str:
    """Decrypt a string, trying current key first then previous key."""
    mf = _get_multi_fernet()
    return mf.decrypt(data.encode()).decode()


def rotate_encryption(encrypted_data: str) -> str:
    """Re-encrypt data with the current key.
    
    Use this to migrate data from old key to new key during rotation.
    
    Args:
        encrypted_data: Data encrypted with any valid key
        
    Returns:
        Data re-encrypted with the current (primary) key
    """
    mf = _get_multi_fernet()
    return mf.rotate(encrypted_data.encode()).decode()

