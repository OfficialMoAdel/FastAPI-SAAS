"""Encryption utilities using cryptography.Fernet."""
import base64
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.app.core.config import settings

logger = logging.getLogger(__name__)

# SECURITY: Generate a random fallback salt per process if not configured
# This ensures dev environments work but production should always set ENCRYPTION_SALT
_FALLBACK_SALT: bytes | None = None


def _get_salt() -> bytes:
    """Get encryption salt from config or generate secure fallback."""
    global _FALLBACK_SALT

    if settings.ENCRYPTION_SALT:
        return settings.ENCRYPTION_SALT.get_secret_value().encode()

    # Fallback: generate random salt per process (NOT recommended for production)
    if _FALLBACK_SALT is None:
        _FALLBACK_SALT = os.urandom(32)
        logger.warning(
            "ENCRYPTION_SALT not configured. Using random per-process salt. "
            "This is insecure for production - encrypted data will be unrecoverable after restart!"
        )
    return _FALLBACK_SALT


def _get_fernet() -> Fernet:
    """
    Get Fernet instance.

    If ENCRYPTION_KEY is not set, we generate a deterministic key based on SECRET_KEY
    to prevent runtime errors in dev, but in prod ENCRYPTION_KEY should be set.
    """
    key_str = settings.ENCRYPTION_KEY.get_secret_value() if settings.ENCRYPTION_KEY else None

    if not key_str:
        # Fallback: derive from SECRET_KEY (not recommended for prod rotation but safe functionality)
        # Using PBKDF2HMAC to ensure we have a URL-safe base64-encoded 32-byte key
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_get_salt(),  # SECURITY FIX: Use config-based salt
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.get_secret_value().encode()))
    else:
        # Ensure it's bytes
        try:
            key = key_str.encode() if isinstance(key_str, str) else key_str
            # Simple validation: Fernet key must be 32 url-safe base64-encoded bytes
            # If the user provided a raw string, we might need to handle it?
            # Fernet(key) checks validity.
        except Exception:
            raise ValueError("Invalid ENCRYPTION_KEY format.")

    return Fernet(key)

def encrypt_data(data: str) -> str:
    """Encrypt a string."""
    f = _get_fernet()
    return f.encrypt(data.encode()).decode()

def decrypt_data(data: str) -> str:
    """Decrypt a string."""
    f = _get_fernet()
    return f.decrypt(data.encode()).decode()
