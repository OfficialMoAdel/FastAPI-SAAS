"""MFA service layer - handles TOTP and backup code authentication.

Provides business logic for multi-factor authentication including:
- TOTP secret generation and verification
- Backup code generation and verification
- MFA enable/disable workflows
"""

import asyncio
import base64
import secrets
from datetime import UTC, datetime
from typing import Any

import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.app.core.config import settings
from src.app.core.exceptions.http_exceptions import BadRequestException, UnauthorizedException
from src.app.core.utils import cache
from src.app.modules.mfa.crud import crud_mfa_methods
from src.app.modules.mfa.models import MFAMethod
from src.app.modules.mfa.schemas import (
    MFAEnableRequest,
    MFAVerifyRequest,
)

logger = get_logger(__name__)

# Constants for MFA configuration - MUST be defined before class usage
# FIXED: Use settings with defaults for MFA lockout configuration
MFA_MAX_ATTEMPTS: int = getattr(settings, "MFA_MAX_ATTEMPTS", 5)
MFA_LOCKOUT_DURATION_SECONDS: int = getattr(settings, "MFA_LOCKOUT_DURATION_SECONDS", 300)
TOTP_INTERVAL = 30  # 30 seconds
TOTP_DIGITS = 6
TOTP_SKEW = 1  # Allow 1 interval before/after
BACKUP_CODE_COUNT = 10
BACKUP_CODE_LENGTH = 10  # Characters after 'BC-' prefix
BACKUP_CODE_PREFIX = "BC-"

# Audit log action names
MFA_ACTION_SETUP = "mfa.setup"
MFA_ACTION_ENABLE = "mfa.enable"
MFA_ACTION_DISABLE = "mfa.disable"
MFA_ACTION_VERIFY_SUCCESS = "mfa.verify.success"
MFA_ACTION_VERIFY_FAILED = "mfa.verify.failed"
MFA_ACTION_BACKUP_CODE_USED = "mfa.backup_code.used"
MFA_ACTION_BACKUP_CODES_REGENERATED = "mfa.backup_codes.regenerated"

# Lua script for atomic MFA lockout check + increment
# Returns: -1 if locked out, otherwise new attempt count
_MFA_CHECK_LUA = """
    local key = KEYS[1]
    local max_attempts = tonumber(ARGV[1])
    local lockout_duration = tonumber(ARGV[2])

    local current = redis.call('GET', key)
    if current and tonumber(current) >= max_attempts then
        local ttl = redis.call('TTL', key)
        if ttl > 0 then
            return -1
        end
    end

    local new_count = redis.call('INCR', key)
    redis.call('EXPIRE', key, lockout_duration)
    return new_count
"""


class MFAService:
    """Service for multi-factor authentication operations."""

    async def generate_totp_secret(self, user_id: int, db: AsyncSession) -> dict[str, str]:
        """Generate a new TOTP secret for user setup.

        Args:
            user_id: The user's ID.
            db: Database session.

        Returns:
            Dict with setup_token and otpauth_url (for QR code).
        """
        import uuid

        # Generate secure random secret
        secret = pyotp.random_base32()

        # Create provisioning URI for QR code
        totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL, digits=TOTP_DIGITS)
        provisioning_uri = totp.provisioning_uri(
            name=f"user_{user_id}@{settings.APP_NAME}",
            issuer_name=settings.APP_NAME,
        )

        logger.info("mfa_setup_initiated", user_id=user_id)

        # Audit log: MFA setup initiated
        try:
            from src.app.core.events import EventPriority, event_bus
            from src.app.modules.users.crud import crud_users

            user = await crud_users.get(db=db, id=user_id)
            if user:
                await event_bus.publish(
                    "audit.log",
                    {
                        "action": MFA_ACTION_SETUP,
                        "entity_type": "mfa",
                        "entity_id": None,
                        "tenant_id": str(user.get("tenant_id", "")),
                        "user_id": str(user_id),
                        "details": {"method": "totp", "status": "initiated"},
                    },
                    priority=EventPriority.NORMAL,
                )
        except Exception as e:
            logger.debug("Audit logging failed (non-critical): %s", e)

        # Store secret temporarily in redis (Expires in 10 minutes)
        setup_token = str(uuid.uuid4())
        if cache.client is not None:
            try:
                await cache.client.setex(f"mfa_setup:{setup_token}", 600, secret)
            except Exception as e:
                logger.error("Redis failed during MFA setup: %s", e)
                from src.app.modules._base.exceptions import ServiceUnavailableError

                raise ServiceUnavailableError("MFA setup is currently unavailable.")
        else:
            from src.app.modules._base.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError("MFA setup is currently unavailable.")

        # Note: In production, generate actual QR code image
        # For now, return the provisioning URI
        return {
            "setup_token": setup_token,
            "otpauth_url": provisioning_uri,
            "qr_code_base64": "",  # Placeholder - would be generated
        }

    async def enable_mfa(
        self,
        user_id: int,
        request: MFAEnableRequest,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Enable MFA for a user after verification.

        Args:
            user_id: The user's ID.
            request: Enable request with setup_token and totp_token.
            db: Database session.

        Returns:
            Dict with backup codes and status.

        Raises:
            BadRequestException: If token is invalid or MFA already enabled.
        """
        # Check if MFA is already enabled
        existing = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")
        if existing:
            raise BadRequestException("MFA is already enabled for this user")

        # Retrieve secret from Redis using setup_token
        mfa_setup_key = f"mfa_setup:{request.setup_token}"
        totp_secret_bytes = await cache.client.get(mfa_setup_key) if cache.client else None

        if not totp_secret_bytes:
            raise BadRequestException("Setup token expired or invalid. Please restart MFA setup.")

        totp_secret = totp_secret_bytes.decode("utf-8") if isinstance(totp_secret_bytes, bytes) else totp_secret_bytes

        # Verify the provided TOTP token
        totp = pyotp.TOTP(totp_secret, interval=TOTP_INTERVAL, digits=TOTP_DIGITS)
        if not totp.verify(request.totp_token, valid_window=TOTP_SKEW):
            raise BadRequestException("Invalid TOTP token. Please try again.")

        # Clean up the setup token
        if cache.client is not None:
            await cache.client.delete(mfa_setup_key)

        # Generate backup codes
        backup_codes = await self._generate_backup_codes()

        # Encrypt secret before storing
        encrypted_secret = await self._encrypt_secret(totp_secret)

        # Hash backup codes for storage concurrently
        backup_codes_hashed = await asyncio.gather(*[self._hash_backup_code(code, user_id) for code in backup_codes])

        # Create MFA method record
        mfa_method = MFAMethod(
            user_id=user_id,
            secret=encrypted_secret,
        )
        mfa_method.backup_codes = backup_codes_hashed

        db.add(mfa_method)
        await db.commit()
        await db.refresh(mfa_method)

        logger.info("mfa_enabled", user_id=user_id, method="totp")

        # Audit log: MFA enabled
        try:
            from src.app.core.events import EventPriority, event_bus
            from src.app.modules.users.crud import crud_users

            user = await crud_users.get(db=db, id=user_id)
            if user:
                await event_bus.publish(
                    "audit.log",
                    {
                        "action": MFA_ACTION_ENABLE,
                        "entity_type": "mfa",
                        "entity_id": str(mfa_method.id),
                        "tenant_id": str(user.get("tenant_id", "")),
                        "user_id": str(user_id),
                        "details": {"method": "totp", "backup_codes_count": len(backup_codes)},
                    },
                    priority=EventPriority.CRITICAL,
                )
        except Exception as e:
            logger.debug("Audit logging failed (non-critical): %s", e)

        return {
            "message": "MFA enabled successfully",
            "backup_codes": backup_codes,
            "backup_codes_remaining": len(backup_codes),
        }

    async def _check_mfa_lockout(self, user_id: int) -> None:
        """Check and increment MFA lockout counter.

        Uses atomic Lua script to check if user is already locked out,
        and if not, increments the attempt counter.

        Raises:
            UnauthorizedException: If user is locked out.
        """
        if cache.client is None:
            # SECURITY FIX: Fail closed during infrastructure disruptions
            from src.app.core.config import settings

            if getattr(settings.auth, "AUDIT_LOG_FAILURE_MODE", "") == "fail_closed":
                logger.error("Redis unavailable - MFA lockout failing closed | user_id=%s", user_id)
                raise UnauthorizedException("Authentication temporarily unavailable. Please try again later.")
            logger.warning("Redis unavailable - MFA lockout disabled (fail open) | user_id=%s", user_id)
            return

        mfa_attempts_key = f"mfa_attempts:{user_id}"
        try:
            raw_result = await cache.client.eval(  # type: ignore[misc]
                _MFA_CHECK_LUA,
                1,
                mfa_attempts_key,
                str(MFA_MAX_ATTEMPTS),
                str(MFA_LOCKOUT_DURATION_SECONDS),
            )
            # Handle potential bytes return from redis.eval
            result = int(raw_result) if raw_result is not None else 0
        except Exception as e:
            from src.app.core.config import settings

            if getattr(settings.auth, "AUDIT_LOG_FAILURE_MODE", "") == "fail_closed":
                logger.error("MFA lockout check failed - failing closed", user_id=user_id, error=str(e))
                raise UnauthorizedException("Authentication temporarily unavailable.")
            logger.error("MFA lockout check failed", user_id=user_id, error=str(e))
            return  # Fail open

        if result == -1:
            logger.warning("mfa_lockout_enforced", user_id=user_id)
            raise UnauthorizedException("MFA locked. Too many failed attempts. Try again later.")

    async def _reset_mfa_lockout(self, user_id: int) -> None:
        """Reset MFA lockout counter after successful verification."""
        if cache.client is not None:
            try:
                await cache.client.delete(f"mfa_attempts:{user_id}")
            except Exception as e:
                logger.debug("Failed to reset MFA lockout counter", user_id=user_id, error=str(e))

    async def verify_totp_token(
        self,
        user_id: int,
        request: MFAVerifyRequest,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Verify a TOTP token for a user.

        Args:
            user_id: The user's ID.
            request: Verify request with totp_token or backup_code.
            db: Database session.

        Returns:
            Dict with verification status.

        Raises:
            UnauthorizedException: If token is invalid or MFA not enabled.
        """
        # Get user's MFA method (FastCRUD returns dict)
        mfa_method_dict: dict[str, Any] | None = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")
        if not mfa_method_dict:
            raise UnauthorizedException("MFA is not enabled for this user")

        # SECURITY FIX: Check and increment MFA lockout AFTER confirming MFA is enabled
        # to prevent DoS against non-MFA users
        await self._check_mfa_lockout(user_id)

        # Check backup code first if provided
        if request.backup_code:
            backup_result = await self._verify_backup_code(user_id, request.backup_code, db)
            if backup_result["valid"]:
                # Lockout is reset inside _verify_backup_code on success
                return backup_result

        # Verify TOTP token if provided
        if request.totp_token:
            # Decrypt secret
            secret = await self._decrypt_secret(mfa_method_dict["secret"])

            # Verify TOTP token
            totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL, digits=TOTP_DIGITS)
            if totp.verify(request.totp_token, valid_window=TOTP_SKEW):
                # Update last_used_at - fetch model instance
                mfa_method = await db.get(MFAMethod, mfa_method_dict["id"])
                if mfa_method:
                    # BUG FIX: Use UTC-aware datetime
                    mfa_method.last_used_at = datetime.now(UTC)
                    await db.commit()

                # Clear lockout counter on successful verification
                await self._reset_mfa_lockout(user_id)

                # Audit log: MFA verification success
                try:
                    from src.app.core.events import EventPriority, event_bus
                    from src.app.modules.users.crud import crud_users

                    user = await crud_users.get(db=db, id=user_id)
                    if user:
                        await event_bus.publish(
                            "audit.log",
                            {
                                "action": MFA_ACTION_VERIFY_SUCCESS,
                                "entity_type": "mfa",
                                "entity_id": str(mfa_method_dict["id"]),
                                "tenant_id": str(user.get("tenant_id", "")),
                                "user_id": str(user_id),
                                "details": {"method": "totp"},
                            },
                            priority=EventPriority.NORMAL,
                        )
                except Exception as e:
                    logger.debug("Audit logging failed (non-critical): %s", e)

                return {
                    "valid": True,
                    "message": "Verification successful",
                    "backup_codes_remaining": None,
                }

        # Audit log: MFA verification failed
        try:
            from src.app.core.events import EventPriority, event_bus
            from src.app.modules.users.crud import crud_users

            user = await crud_users.get(db=db, id=user_id)
            if user:
                await event_bus.publish(
                    "audit.log",
                    {
                        "action": MFA_ACTION_VERIFY_FAILED,
                        "entity_type": "mfa",
                        "entity_id": str(mfa_method_dict["id"]),
                        "tenant_id": str(user.get("tenant_id", "")),
                        "user_id": str(user_id),
                        "details": {"method": "totp" if request.totp_token else "backup_code"},
                    },
                    priority=EventPriority.NORMAL,
                )
        except Exception as e:
            logger.debug("Audit logging failed (non-critical): %s", e)

        raise UnauthorizedException("Invalid MFA token")

    async def _verify_backup_code(
        self,
        user_id: int,
        code: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Internal method to verify backup code.

        Args:
            user_id: The user's ID.
            code: The backup code to verify.
            db: Database session.

        Returns:
            Dict with verification status.
        """
        from src.app.core.security import verify_password

        # Normalize code format
        normalized_code = code.upper().replace("-", "")

        # Strip the "BC" prefix if present. We check len > 10 to ensure we don't
        # falsely strip a generated 10-char hex code that legitimately starts with "BC".
        if normalized_code.startswith("BC") and len(normalized_code) > 10:
            normalized_code = normalized_code[2:]

        # SECURITY FIX: Use row-level locking to prevent race conditions (double spending)
        # Fetch MFA method with FOR UPDATE to lock the row during verification
        stmt = select(MFAMethod).where(MFAMethod.user_id == user_id, MFAMethod.type == "totp").with_for_update()
        result = await db.execute(stmt)
        mfa_method = result.scalar_one_or_none()

        if not mfa_method or not mfa_method.backup_codes:
            return {"valid": False, "message": "Invalid code", "backup_codes_remaining": None}

        # Reconstruct full code with prefix for verification
        formatted_code = f"{BACKUP_CODE_PREFIX}{normalized_code[:5]}-{normalized_code[5:]}"

        matched_hash = None

        # We now use fast SHA256 hashing to prevent bcrypt DoS when iterating 10 codes
        hashed_input = await self._hash_backup_code(formatted_code, user_id)

        # Pre-compute old SHA256 hash for legacy fallback
        import hashlib

        old_salt = settings.SECRET_KEY.get_secret_value().encode()
        old_hashed_input = hashlib.sha256(old_salt + formatted_code.encode()).hexdigest()

        import hmac as hmac_mod

        for stored_hash in mfa_method.backup_codes:
            # Fallback for old bcrypt hashes dynamically
            if stored_hash.startswith("$2b$"):
                if await verify_password(formatted_code, stored_hash):
                    matched_hash = stored_hash
                    break
            # Fast HMAC-SHA256
            elif hmac_mod.compare_digest(hashed_input, stored_hash):
                matched_hash = stored_hash
                break
            # Legacy simple SHA256 fallback
            elif hmac_mod.compare_digest(old_hashed_input, stored_hash):
                matched_hash = stored_hash
                break

        if matched_hash:
            # Remove used backup code (we have the lock, so this is safe)
            # Create a new list to ensure SQLAlchemy detects the change (for JSON/ARRAY types)
            new_backup_codes = list(mfa_method.backup_codes)
            new_backup_codes.remove(matched_hash)
            mfa_method.backup_codes = new_backup_codes

            # BUG FIX: Use UTC-aware datetime (was naive)
            mfa_method.last_used_at = datetime.now(UTC)
            await db.commit()

            # Clear lockout counter on successful backup code use so the user isn't
            # locked out on their very next MFA verification attempt.
            if cache.client is not None:
                try:
                    await cache.client.delete(f"mfa_attempts:{user_id}")
                except Exception:
                    pass  # Non-critical

            remaining = len(mfa_method.backup_codes)
            logger.info("mfa_backup_code_used", user_id=user_id, remaining=remaining)

            # Audit log: Backup code used
            try:
                from src.app.core.events import EventPriority, event_bus
                from src.app.modules.users.crud import crud_users

                user = await crud_users.get(db=db, id=user_id)
                if user:
                    await event_bus.publish(
                        "audit.log",
                        {
                            "action": MFA_ACTION_BACKUP_CODE_USED,
                            "entity_type": "mfa",
                            "entity_id": str(mfa_method.id),
                            "tenant_id": str(user.get("tenant_id", "")),
                            "user_id": str(user_id),
                            "details": {"backup_codes_remaining": remaining},
                        },
                        priority=EventPriority.CRITICAL,
                    )
            except Exception as e:
                logger.debug("Audit logging failed (non-critical): %s", e)

            return {
                "valid": True,
                "message": "Verification successful",
                "backup_codes_remaining": remaining,
            }

        return {"valid": False, "message": "Invalid code", "backup_codes_remaining": None}

    async def disable_mfa(
        self,
        user_id: int,
        password: str,
        totp_token: str | None,
        backup_code: str | None,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Disable MFA for a user.

        Args:
            user_id: The user's ID.
            password: User's password for verification.
            totp_token: Optional TOTP token for additional verification.
            db: Database session.

        Returns:
            Dict with success message.

        Raises:
            BadRequestException: If MFA is not enabled.
            UnauthorizedException: If verification fails.
        """
        from src.app.core.security import verify_password
        from src.app.modules.users.crud import crud_users

        # Verify user exists and password is correct
        user = await crud_users.get(db=db, id=user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("Invalid credentials")

        if not await verify_password(password, user.get("hashed_password", "")):
            raise UnauthorizedException("Invalid credentials")

        # Get user's MFA method (FastCRUD returns dict)
        mfa_method_dict: dict[str, Any] | None = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")
        if not mfa_method_dict:
            raise BadRequestException("MFA is not enabled for this user")

        # SECURITY FIX: Enforce cryptographic MFA verification before stripping MFA state.
        # This replaces the optional check to prevent an MFA bypass where someone with
        # just an active token could unbind MFA without the second factor.
        verify_req = MFAVerifyRequest(
            totp_token=totp_token,
            backup_code=backup_code,
        )
        await self.verify_totp_token(user_id=user_id, request=verify_req, db=db)

        # Delete MFA method
        await crud_mfa_methods.delete(db=db, id=mfa_method_dict["id"])

        logger.info("mfa_disabled", user_id=user_id)

        # Audit log: MFA disabled
        try:
            from src.app.core.events import EventPriority, event_bus

            await event_bus.publish(
                "audit.log",
                {
                    "action": MFA_ACTION_DISABLE,
                    "entity_type": "mfa",
                    "entity_id": str(mfa_method_dict["id"]),
                    "tenant_id": str(user.get("tenant_id", "")),
                    "user_id": str(user_id),
                    "details": {"method": "totp"},
                },
                priority=EventPriority.CRITICAL,
            )
        except Exception as e:
            logger.debug("Audit logging failed (non-critical): %s", e)

        return {"message": "MFA disabled successfully"}

    async def is_mfa_enabled(self, user_id: int, db: AsyncSession) -> bool:
        """Check if MFA is enabled for a user.

        Args:
            user_id: The user's ID.
            db: Database session.

        Returns:
            True if MFA is enabled, False otherwise.
        """
        mfa_method_dict: dict[str, Any] | None = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")
        return mfa_method_dict is not None

    async def get_mfa_status(
        self,
        user_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Get MFA status for a user.

        Args:
            user_id: The user's ID.
            db: Database session.

        Returns:
            Dict with MFA status information.
        """
        mfa_method_dict: dict[str, Any] | None = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")

        if not mfa_method_dict:
            return {
                "is_enabled": False,
                "backup_codes_remaining": None,
                "last_used": None,
            }

        last_used = None
        if mfa_method_dict.get("last_used_at"):
            last_used = mfa_method_dict["last_used_at"].isoformat()

        remaining = 0
        backup_codes = mfa_method_dict.get("backup_codes")
        if backup_codes:
            remaining = len(backup_codes)

        return {
            "is_enabled": True,
            "backup_codes_remaining": remaining,
            "last_used": last_used,
        }

    async def regenerate_backup_codes(
        self,
        user_id: int,
        password: str,
        totp_token: str | None,
        backup_code: str | None,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Generate new backup codes for a user.

        Args:
            user_id: The user's ID.
            password: User's password for verification.
            totp_token: Optional TOTP token for additional verification.
            db: Database session.

        Returns:
            Dict with new backup codes.

        Raises:
            BadRequestException: If MFA is not enabled.
            UnauthorizedException: If verification fails.
        """
        from src.app.core.security import verify_password
        from src.app.modules.users.crud import crud_users

        # Verify user exists and password is correct
        user = await crud_users.get(db=db, id=user_id, is_deleted=False)
        if not user:
            raise UnauthorizedException("Invalid credentials")

        if not await verify_password(password, user.get("hashed_password", "")):
            raise UnauthorizedException("Invalid credentials")

        # Get user's MFA method (FastCRUD returns dict)
        mfa_method_dict: dict[str, Any] | None = await crud_mfa_methods.get(db=db, user_id=user_id, type="totp")
        if not mfa_method_dict:
            raise BadRequestException("MFA is not enabled for this user")

        # SECURITY FIX: Enforce cryptographic MFA verification before changing state.
        # This inherently handles check_mfa_lockout + increments, checking TOTP ranges,
        # fallback backup code validation with DB locks, and structured logging.
        verify_req = MFAVerifyRequest(
            totp_token=totp_token,
            backup_code=backup_code,
        )
        await self.verify_totp_token(user_id=user_id, request=verify_req, db=db)

        # Generate new backup codes and hash them concurrently
        backup_codes = await self._generate_backup_codes()
        backup_codes_hashed = list(
            await asyncio.gather(*[self._hash_backup_code(code, user_id) for code in backup_codes])
        )

        # Fetch model instance to update
        mfa_method = await db.get(MFAMethod, mfa_method_dict["id"])
        if mfa_method:
            mfa_method.backup_codes = backup_codes_hashed
            await db.commit()

        logger.info("mfa_backup_codes_regenerated", user_id=user_id)

        # Audit log: Backup codes regenerated
        try:
            from src.app.core.events import EventPriority, event_bus

            await event_bus.publish(
                "audit.log",
                {
                    "action": MFA_ACTION_BACKUP_CODES_REGENERATED,
                    "entity_type": "mfa",
                    "entity_id": str(mfa_method_dict["id"]),
                    "tenant_id": str(user.get("tenant_id", "")),
                    "user_id": str(user_id),
                    "details": {"backup_codes_count": len(backup_codes)},
                },
                priority=EventPriority.NORMAL,
            )
        except Exception as e:
            logger.debug("Audit logging failed (non-critical): %s", e)

        return {
            "message": "New backup codes generated",
            "backup_codes": backup_codes,
        }

    async def _generate_backup_codes(self) -> list[str]:
        """Generate a list of backup codes.

        Returns:
            List of backup codes in format 'BC-XXXXX-XXXXX'.
        """
        codes = []
        for _ in range(BACKUP_CODE_COUNT):
            # Generate random 10-character code
            code_part = secrets.token_hex(5).upper()  # 10 hex chars
            formatted_code = f"{BACKUP_CODE_PREFIX}{code_part[:5]}-{code_part[5:]}"
            codes.append(formatted_code)
        return codes

    async def _hash_backup_code(self, code: str, user_id: int) -> str:
        """Hash a backup code for secure storage using HMAC-SHA256.

        Args:
            code: The backup code to hash.
            user_id: The ID of the user (used as salt).

        Returns:
            Hashed code string.
        """
        import hashlib
        import hmac

        # Use ENCRYPTION_KEY if available, fallback to SECRET_KEY for backward compatibility
        base_key = (
            settings.ENCRYPTION_KEY.get_secret_value()
            if settings.ENCRYPTION_KEY
            else settings.SECRET_KEY.get_secret_value()
        )
        salt = f"{base_key}_{user_id}".encode()
        return hmac.new(salt, code.encode(), hashlib.sha256).hexdigest()

    _fernet_instance: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        """Get Fernet instance for encryption/decryption.

        Derives a valid Fernet key from SECRET_KEY using PBKDF2HMAC.
        Uses proper key derivation with salt instead of simple truncation.

        Returns:
            Fernet instance.
        """
        if MFAService._fernet_instance is not None:
            return MFAService._fernet_instance

        # FIXED: Use proper PBKDF2HMAC key derivation with salt
        # instead of simple truncation + null padding (security vulnerability)

        # Use a consistent salt derived from ENCRYPTION_SALT or fallback
        salt_str = (
            settings.ENCRYPTION_SALT.get_secret_value()
            if settings.ENCRYPTION_SALT
            else settings.SECRET_KEY.get_secret_value()
        )
        salt = salt_str.encode()[:16]
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # OWASP recommended minimum
        )
        base_key = (
            settings.ENCRYPTION_KEY.get_secret_value()
            if settings.ENCRYPTION_KEY
            else settings.SECRET_KEY.get_secret_value()
        )
        secret = base_key.encode()
        key = base64.urlsafe_b64encode(kdf.derive(secret))
        MFAService._fernet_instance = Fernet(key)
        return MFAService._fernet_instance

    async def _encrypt_secret(self, secret: str) -> str:
        """Encrypt TOTP secret for storage using Fernet.

        Args:
            secret: The TOTP secret to encrypt.

        Returns:
            Encrypted secret string (base64-encoded).
        """
        fernet = self._get_fernet()
        encrypted = fernet.encrypt(secret.encode())
        return encrypted.decode()

    async def _decrypt_secret(self, encrypted: str) -> str:
        """Decrypt TOTP secret from storage using Fernet.

        Args:
            encrypted: The encrypted secret (base64-encoded).

        Returns:
            Decrypted secret string.
        """
        fernet = self._get_fernet()
        decrypted = fernet.decrypt(encrypted.encode())
        return decrypted.decode()


# Dependency injection factory
def get_mfa_service() -> MFAService:
    """Factory function to get MFAService instance.

    Returns:
        MFAService instance.
    """
    return MFAService()
