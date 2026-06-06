"""Passkey service for WebAuthn registration and authentication.

Handles:
- Challenge generation
- Registration verification
- Authentication challenge generation
- Authentication assertion verification
- Credential management
- Limit enforcement
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
    PublicKeyCredentialType,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from src.app.core.config import EnvironmentOption, settings
from src.app.core.exceptions.http_exceptions import BadRequestException, NotFoundException
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.modules.passkey.crud import crud_passkey_credentials
from src.app.modules.passkey.schemas import PasskeyCredentialCreate

logger = get_logger(__name__)

# Lua script for atomic GETDEL (get and delete) to prevent race conditions.
_GETDEL_LUA = """
    local value = redis.call('GET', KEYS[1])
    if value then
        redis.call('DEL', KEYS[1])
    end
    return value
"""

# Constants
CHALLENGE_TTL = 300  # 5 minutes
AUTH_CHALLENGE_TTL = 300  # 5 minutes for authentication challenges


def _get_rp_id() -> str:
    """Get the relying party ID based on environment."""
    if settings.ENVIRONMENT == EnvironmentOption.LOCAL:
        return "localhost"
    # Extract domain from FRONTEND_URL (e.g., https://example.com -> example.com)
    from urllib.parse import urlparse

    parsed = urlparse(settings.FRONTEND_URL)
    return parsed.netloc or "localhost"


RP_NAME = settings.APP_NAME


class PasskeyService:
    """Service for handling passkey operations."""

    async def generate_registration_challenge(
        self,
        db: AsyncSession,
        user: dict[str, Any],
        device_type: str,
        redis: Redis | None = None,
    ) -> dict[str, Any]:
        """Generate WebAuthn registration options (challenge).

        Args:
            db: Database session.
            user: User dictionary (from get_current_user).
            device_type: User-provided device name.
            redis: Redis client for storing challenge.

        Returns:
            dict: WebAuthn registration options.

        Raises:
            BadRequestException: If user has reached passkey limit.
        """
        user_id = user["id"]

        # 1. Check passkey limit
        if not await crud_passkey_credentials.can_create(db=db, user_id=user_id):
            raise BadRequestException(
                detail=f"Maximum number of passkeys ({crud_passkey_credentials.MAX_PASSKEYS_PER_USER}) reached."
            )

        # 2. Get existing credentials to exclude
        existing_creds = await self._get_existing_credentials(db, user_id)

        # 3. Generate registration options
        options: PublicKeyCredentialCreationOptions = generate_registration_options(
            rp_id=_get_rp_id(),
            rp_name=RP_NAME,
            user_id=str(user_id).encode(),
            user_name=user["email"],
            user_display_name=user.get("name") or user["username"],
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                # BUG FIX: CROSS_PLATFORM was hardcoded, locking out platform authenticators
                # (Touch ID, Face ID, Windows Hello). Removing the constraint allows users
                # to register any authenticator — platform or cross-platform.
                authenticator_attachment=None,
                user_verification=UserVerificationRequirement.PREFERRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            exclude_credentials=existing_creds,
        )

        # 4. Store challenge in Redis
        if redis:
            await self._store_challenge(redis, user_id, options.challenge, device_type)

        result: dict[str, Any] = json_loads(options_to_json(options))
        return result

    async def verify_registration(
        self,
        db: AsyncSession,
        user: dict[str, Any],
        credential: dict[str, Any],
        redis: Redis | None = None,
    ) -> dict[str, Any]:
        """Verify WebAuthn registration response and store credential.

        Args:
            db: Database session.
            user: User dictionary.
            credential: The WebAuthn response from the client.
            redis: Redis client.

        Returns:
            dict: Created credential information.

        Raises:
            BadRequestException: If verification fails or challenge expired.
        """
        user_id = user["id"]

        # 1. Retrieve stored challenge
        stored_data = await self._get_stored_challenge(redis, user_id) if redis else None
        if not stored_data:
            raise BadRequestException(detail="Registration challenge expired or invalid.")

        challenge = stored_data["challenge"]
        device_type = stored_data["device_type"]

        # 2. Verify registration response
        try:
            from webauthn.helpers import parse_registration_credential_json

            # Parse dict into RegistrationCredential (handles snake_case conversion and base64 decoding)
            parsed_credential = parse_registration_credential_json(credential)

            verification = self._verify_with_webauthn(
                credential=parsed_credential,
                expected_challenge=challenge,
                expected_origin=self._get_origin(),
                expected_rp_id=_get_rp_id(),
            )
        except Exception as e:
            logger.warning("Passkey verification failed for user :", user_id=user_id, error=e)
            raise BadRequestException(detail="Passkey verification failed.")

        # 3. Encrypt sensitive data
        import hashlib

        from src.app.core.security.encryption import get_encryption

        encryption = get_encryption()
        encrypted_cred_id = encryption.encrypt_credential(verification.credential_id)
        encrypted_public_key = encryption.encrypt_credential(verification.credential_public_key)

        # Compute SHA256 hash of decrypted credential_id for O(log n) lookup
        credential_id_hash = hashlib.sha256(verification.credential_id).hexdigest()

        # 4. Store credential in DB
        cred_in = PasskeyCredentialCreate(
            user_id=user_id,
            credential_id=encrypted_cred_id,
            credential_id_hash=credential_id_hash,
            public_key=encrypted_public_key,
            counter=verification.sign_count,
            device_type=device_type,
            aaguid=verification.aaguid,
            transports=credential.get("response", {}).get("transports"),
        )

        created_cred = await self._store_credential(db, cred_in)

        # 5. Cleanup - Handled atomically by _get_stored_challenge

        return created_cred

    async def list_credentials(self, db: AsyncSession, user_id: int) -> list[Any]:
        """List all passkey credentials for a user."""
        from src.app.core.tenancy.context import get_current_tenant
        from src.app.modules.users.crud import crud_users

        tenant = get_current_tenant()
        if tenant:
            user = await crud_users.get(db=db, id=user_id)
            if user and user.get("tenant_id") != tenant.id:
                raise NotFoundException(detail="Passkey credentials not found.")

        return await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)

    async def delete_credential(self, db: AsyncSession, user_id: int, credential_id: int) -> None:
        """Delete a passkey credential."""
        from src.app.core.tenancy.context import get_current_tenant
        from src.app.modules.users.crud import crud_users

        tenant = get_current_tenant()
        if tenant:
            user = await crud_users.get(db=db, id=user_id)
            if user and user.get("tenant_id") != tenant.id:
                raise NotFoundException(detail="Passkey credential not found.")

        cred = await crud_passkey_credentials.get_credential(db=db, id=credential_id)
        if not cred or cred.user_id != user_id:
            raise NotFoundException(detail="Passkey credential not found.")

        await crud_passkey_credentials.delete_credential(db=db, db_object=cred)

    # ============================================================================
    # Authentication Methods (T079, T080)
    # ============================================================================

    async def generate_authentication_challenge(
        self,
        db: AsyncSession,
        redis: Redis | None = None,
        email: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Generate WebAuthn authentication options (challenge) for login.

        Args:
            db: Database session.
            redis: Redis client for storing challenge.
            email: Optional email hint to filter allowed credentials (for discoverable credentials).

        Returns:
            tuple: (WebAuthn authentication options dict, challenge_id for verification).
        """
        import uuid

        # 1. Get allowed credentials if email provided
        allowed_credentials: list[PublicKeyCredentialDescriptor] = []
        if email:
            allowed_credentials = await self._get_credentials_for_email(db, email)

        # 2. Generate authentication options
        options: PublicKeyCredentialRequestOptions = generate_authentication_options(
            rp_id=_get_rp_id(),
            user_verification=UserVerificationRequirement.PREFERRED,
            allow_credentials=allowed_credentials if allowed_credentials else None,
        )

        # 3. Store challenge in Redis with unique challenge_id
        challenge_id = str(uuid.uuid4())
        if redis:
            await self._store_auth_challenge(redis, challenge_id, options.challenge)

        result: dict[str, Any] = json_loads(options_to_json(options))
        return result, challenge_id

    async def verify_authentication(
        self,
        db: AsyncSession,
        redis: Redis | None = None,
        credential: dict[str, Any] | None = None,
        challenge_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify WebAuthn authentication response and return JWT tokens.

        Args:
            db: Database session.
            redis: Redis client.
            credential: The WebAuthn assertion response from the client.
            challenge_id: The challenge_id returned from generate_authentication_challenge.

        Returns:
            dict: Authentication tokens (access_token, refresh_token, etc.).

        Raises:
            BadRequestException: If verification fails, challenge expired, or replay attempt.
        """
        if not credential:
            raise BadRequestException(detail="Credential is required.")

        if not challenge_id:
            raise BadRequestException(detail="challenge_id is required.")

        # 1. Retrieve stored challenge
        stored_challenge = await self._get_stored_auth_challenge(redis, challenge_id) if redis else None
        if not stored_challenge:
            raise BadRequestException(detail="Authentication challenge expired or invalid.")

        # 2. Find credential in database by raw ID
        raw_id = credential.get("rawId") or credential.get("id")
        if not raw_id:
            raise BadRequestException(detail="Credential ID is required.")
        db_credential = await self._find_credential_by_id(db, raw_id)
        if not db_credential:
            raise BadRequestException(detail="Credential not found.")

        # 3. Verify authentication response
        try:
            verification = self._verify_authentication_response(
                credential=credential,
                expected_challenge=stored_challenge,
                expected_origin=self._get_origin(),
                expected_rp_id=_get_rp_id(),
                db_credential=db_credential,
            )
        except Exception as e:
            logger.warning("Passkey authentication failed", error=e)
            raise BadRequestException(detail="Passkey authentication failed.")

        # 4. Check counter for replay prevention
        new_count = verification.new_sign_count
        # BUG FIX: Per WebAuthn spec §6.1, a new_count of 0 means the authenticator
        # does not support counters at all (it always returns 0). In that case we must
        # NOT treat it as a replay attack, even if stored counter is also 0.
        # Only flag replays when both old AND new counts are non-zero.
        if (new_count != 0 or db_credential.counter != 0) and new_count <= db_credential.counter:
            logger.warning(
                f"Possible replay attack for credential {db_credential.id}: "
                f"stored counter={db_credential.counter}, received={new_count}"
            )
            raise BadRequestException(detail="Counter mismatch - possible replay attack.")

        # 5. Update counter and last_used_at
        await self._update_credential_counter(db, db_credential, new_count)

        # 6. Get user and generate tokens
        user = await self._get_user_for_credential(db, db_credential)
        if not user:
            raise BadRequestException(detail="User not found for credential.")

        tokens = await self._generate_auth_tokens(user)

        # 7. Cleanup - Handled atomically by _get_stored_auth_challenge

        return tokens

    # ============================================================================
    # Internal Helpers
    # ============================================================================

    async def _decrypt_credentials_parallel(
        self,
        credentials_data: list[tuple[bytes, Any | None]],
        user_identifier: int | str,
    ) -> list[PublicKeyCredentialDescriptor]:
        """Decrypt credentials in parallel with concurrency control.

        Args:
            credentials_data: List of (encrypted_id, transports) tuples.
            user_identifier: User ID or email for logging.

        Returns:
            List of decrypted PublicKeyCredentialDescriptor objects.
        """
        from src.app.core.monitoring.r1_unified_metrics import record_passkey_query
        from src.app.core.security.encryption import get_encryption

        if not credentials_data:
            return []

        encryption = get_encryption()
        start_time = time.perf_counter()

        # P2-6: Use configurable concurrency limit from settings
        from src.app.core.config import settings

        concurrency_limit = settings.PASSKEY_DECRYPT_CONCURRENCY_LIMIT
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def decrypt_one(cred_id: bytes, transports: Any | None) -> PublicKeyCredentialDescriptor | None:
            async with semaphore:
                try:
                    decrypted_id = await asyncio.to_thread(encryption.decrypt_credential, cred_id)
                    logger.debug("Successfully decrypted credential for %s", user_identifier)
                    return PublicKeyCredentialDescriptor(
                        type=PublicKeyCredentialType.PUBLIC_KEY,
                        id=decrypted_id,
                        transports=transports,
                    )
                except Exception as e:
                    logger.debug("Failed to decrypt credential for %s: %s", user_identifier, str(e))
                    return None

        # Decrypt all credentials in parallel
        results = await asyncio.gather(*[decrypt_one(cid, tr) for cid, tr in credentials_data])

        # Filter out None values
        valid_results = [r for r in results if r is not None]

        # Log metrics
        elapsed_seconds = time.perf_counter() - start_time
        logger.info(
            "Decrypted %d/%d credentials in %.2fms (parallel) for %s",
            len(valid_results),
            len(credentials_data),
            elapsed_seconds * 1000,
            user_identifier,
        )
        record_passkey_query(elapsed_seconds)

        return valid_results

    async def _get_existing_credentials(self, db: AsyncSession, user_id: int) -> list[Any]:
        """Get existing credential IDs for exclusion with parallel decryption."""
        from sqlalchemy import select

        from src.app.core.config import settings
        from src.app.modules.passkey.models import PasskeyCredential

        # P3-2 Optimization: Column-specific SELECT for credential_id only

        creds_to_decrypt: list[tuple[bytes, list[str] | None]]
        if settings.PASSKEY_USE_COLUMN_SELECT:
            stmt = (
                select(PasskeyCredential.credential_id)
                .where(PasskeyCredential.user_id == user_id)
                .order_by(PasskeyCredential.created_at.desc())
                .limit(settings.PASSKEY_MAX_CREDENTIALS_PER_USER)
            )
            result = await db.execute(stmt)
            # Format: list of (id, None)
            creds_to_decrypt = [(row[0], None) for row in result.fetchall()]
        else:
            # Fallback path
            creds = await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)
            if len(creds) > settings.PASSKEY_MAX_CREDENTIALS_PER_USER:
                creds = creds[: settings.PASSKEY_MAX_CREDENTIALS_PER_USER]

            # Format: list of (id, transports)
            creds_to_decrypt = [(c.credential_id, c.transports if c.transports else None) for c in creds]

        # Use shared helper
        return await self._decrypt_credentials_parallel(creds_to_decrypt, user_id)

    async def _store_challenge(self, redis: Redis, user_id: int, challenge: bytes, device_type: str) -> None:
        """Store registration challenge in Redis."""
        key = f"passkey:challenge:{user_id}"
        data = {
            "challenge": bytes_to_base64url(challenge),
            "device_type": device_type,
        }
        await redis.setex(key, CHALLENGE_TTL, json_dumps(data, as_str=True))

    async def _get_stored_challenge(self, redis: Redis, user_id: int) -> dict[str, Any] | None:
        """Retrieve and atomically delete registration challenge from Redis."""
        key = f"passkey:challenge:{user_id}"
        # Atomic get and delete to prevent race conditions
        data = await redis.eval(_GETDEL_LUA, 1, key)  # type: ignore[misc]
        if not data:
            return None

        parsed: dict[str, Any] = json_loads(data)
        # Convert challenge back to bytes (base64url decoded) from stored string
        # Note: webauthn expects bytes for expected_challenge
        from webauthn.helpers import base64url_to_bytes

        parsed["challenge"] = base64url_to_bytes(parsed["challenge"])
        return parsed

    async def _delete_challenge(self, redis: Redis, user_id: int) -> None:
        """Delete registration challenge from Redis."""
        key = f"passkey:challenge:{user_id}"
        await redis.delete(key)

    async def _store_credential(self, db: AsyncSession, cred_in: PasskeyCredentialCreate) -> dict[str, Any]:
        """Store new credential in database."""
        result = await crud_passkey_credentials.create(db=db, object=cred_in)
        # Return as dict for API response
        return {
            "id": result.id,
            "device_type": result.device_type,
            "created_at": result.created_at,
            "last_used_at": result.last_used_at,
            "transports": result.transports,
        }

    def _get_origin(self) -> str:
        """Get expected origin based on configured frontend URL."""
        return str(settings.FRONTEND_URL).rstrip("/")

    async def _count_user_passkeys(self, db: AsyncSession, user_id: int) -> int:
        """Count user passkeys."""
        return await crud_passkey_credentials.count_by_user(db=db, user_id=user_id)

    def _verify_with_webauthn(self, **kwargs) -> Any:
        """Wrap webauthn verification for easier mocking."""
        return verify_registration_response(**kwargs)

    # ============================================================================
    # Authentication Helpers (T079, T080)
    # ============================================================================

    async def _get_credentials_for_email(self, db: AsyncSession, email: str) -> list[PublicKeyCredentialDescriptor]:
        """Get passkey credentials for a user by email with parallel decryption."""
        from sqlalchemy import select

        from src.app.core.config import settings
        from src.app.modules.passkey.models import PasskeyCredential
        from src.app.modules.users.crud import crud_users

        # Find user by email
        user = await crud_users.get(db=db, email=email)
        if not user:
            return []

        user_id = user["id"]

        # P3-2 Optimization: Column-specific SELECT for credential_id and transports only

        if settings.PASSKEY_USE_COLUMN_SELECT:
            stmt = (
                select(PasskeyCredential.credential_id, PasskeyCredential.transports)
                .where(PasskeyCredential.user_id == user_id)
                .order_by(PasskeyCredential.created_at.desc())
                .limit(settings.PASSKEY_MAX_CREDENTIALS_PER_USER)
            )
            result = await db.execute(stmt)
            # Format: list of (id, transports)
            creds_to_decrypt = [(row[0], row[1]) for row in result.fetchall()]
        else:
            # Fallback path
            creds = await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)
            if len(creds) > settings.PASSKEY_MAX_CREDENTIALS_PER_USER:
                creds = creds[: settings.PASSKEY_MAX_CREDENTIALS_PER_USER]

            # Format: list of (id, transports)
            creds_to_decrypt = [(c.credential_id, c.transports if c.transports else None) for c in creds]

        # Use shared helper
        return await self._decrypt_credentials_parallel(creds_to_decrypt, email)

    async def _store_auth_challenge(self, redis: Redis, challenge_id: str, challenge: bytes) -> None:
        """Store authentication challenge in Redis, keyed by unique challenge_id."""
        key = f"passkey:auth_challenge:{challenge_id}"
        await redis.setex(key, AUTH_CHALLENGE_TTL, bytes_to_base64url(challenge))

    async def _get_stored_auth_challenge(self, redis: Redis, challenge_id: str) -> bytes | None:
        """Retrieve and atomically delete authentication challenge from Redis."""
        key = f"passkey:auth_challenge:{challenge_id}"
        # Atomic get and delete to prevent race conditions
        data = await redis.eval(_GETDEL_LUA, 1, key)  # type: ignore[misc]
        if not data:
            return None
        return base64url_to_bytes(data)

    async def _delete_auth_challenge(self, redis: Redis, challenge_id: str) -> None:
        """Delete authentication challenge from Redis by challenge_id."""
        key = f"passkey:auth_challenge:{challenge_id}"
        await redis.delete(key)

    async def _find_credential_by_id(self, db: AsyncSession, raw_id: str) -> Any | None:
        """Find a passkey credential by its raw ID (base64url encoded).

        Uses SHA256 hash index for O(log n) lookup instead of O(n) full table scan.
        Falls back to decryption-based search for credentials without hash (migration).
        """
        import hashlib

        from sqlalchemy import select

        from src.app.core.security.encryption import get_encryption
        from src.app.modules.passkey.models import PasskeyCredential

        # Decode the raw_id from base64url
        try:
            credential_id_bytes = base64url_to_bytes(raw_id)
        except Exception:
            return None

        # Compute SHA256 hash for indexed lookup
        target_hash = hashlib.sha256(credential_id_bytes).hexdigest()

        # Primary path: O(log n) hash-based lookup
        stmt = select(PasskeyCredential).where(PasskeyCredential.credential_id_hash == target_hash)
        result = await db.execute(stmt)
        cred = result.scalar_one_or_none()

        if cred is None:
            return None

        # Defense in depth: verify the hash matches decrypted credential
        # This catches hash collisions (astronomically rare for SHA256)
        encryption = get_encryption()
        try:
            decrypted_id = encryption.decrypt_credential(cred.credential_id)
            if decrypted_id == credential_id_bytes:
                return cred
        except Exception as e:
            # Decryption failure - credential may be corrupted
            logger.error(
                "Failed to decrypt passkey credential_id", exc_info=True, extra={"raw_id": raw_id, "error": str(e)}
            )
            pass

        return None

    def _verify_authentication_response(
        self,
        credential: dict[str, Any],
        expected_challenge: bytes,
        expected_origin: str,
        expected_rp_id: str,
        db_credential: Any,
    ) -> Any:
        """Verify WebAuthn authentication assertion."""
        from webauthn.helpers import parse_authentication_credential_json

        from src.app.core.security.encryption import get_encryption

        # Parse the credential
        parsed_credential = parse_authentication_credential_json(credential)

        # Decrypt the public key from database
        encryption = get_encryption()
        credential_public_key = encryption.decrypt_credential(db_credential.public_key)

        # Verify the assertion
        return verify_authentication_response(
            credential=parsed_credential,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            credential_public_key=credential_public_key,
            credential_current_sign_count=db_credential.counter,
        )

    async def _update_credential_counter(self, db: AsyncSession, credential: Any, new_counter: int) -> None:
        """Update credential counter and last_used_at timestamp."""
        credential.counter = new_counter
        credential.last_used_at = datetime.now(UTC)
        await db.commit()

    async def _get_user_for_credential(self, db: AsyncSession, credential: Any) -> dict[str, Any] | None:
        """Get the user associated with a passkey credential with tier eager-loaded."""
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from src.app.modules.users.models import User

        # Query user by ID with tier eager-loaded
        stmt = (
            select(User)
            .options(joinedload(User.tier))
            .where(User.id == credential.user_id, User.is_deleted.is_(False), User.is_active.is_(True))
        )
        result = await db.execute(stmt)
        user = result.unique().scalar_one_or_none()

        if not user:
            return None

        # Direct dict construction for consistency with auth service patterns
        user_dict: dict[str, Any] = {
            "id": user.id,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "name": user.name,
            "username": user.username,
            "email": user.email,
            "hashed_password": user.hashed_password,
            "profile_image_url": user.profile_image_url,
            "tier_id": user.tier_id,
            "tenant_id": user.tenant_id,
            "is_superuser": user.is_superuser,
            "is_active": user.is_active,  # BUG FIX: was missing, causing user.get("is_active") to return None
            "is_deleted": user.is_deleted,
            "is_email_verified": user.is_email_verified,
            # BUG FIX: token_version was missing. Without it, _generate_auth_tokens
            # defaults to 0, meaning revoked sessions (via token_version increment)
            # could still be accepted if the user re-authenticates via passkey.
            "token_version": user.token_version,
        }

        if user.tier:
            user_dict["tier"] = {
                "id": user.tier.id,
                "created_at": user.tier.created_at,
                "updated_at": user.tier.updated_at,
                "name": user.tier.name,
            }
        else:
            user_dict["tier"] = None

        return user_dict

    async def _generate_auth_tokens(self, user: dict) -> dict[str, Any]:
        """Generate JWT authentication tokens for a user with tier info for rate limiting."""
        from src.app.core.security import create_access_token, create_refresh_token

        # Include tier info in JWT claims for Zero-DB rate limiting
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None

        # Consistent with standard login, we need to include token_version and tenant_id
        token_data = {
            "sub": user.get("username") or user.get("email"),
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
            "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
            "token_version": user.get("token_version", 0),
        }

        access_token = await create_access_token(data=token_data)
        refresh_token = await create_refresh_token(data=token_data)

        try:
            import json

            import jwt

            from src.app.core.security.jwt_utils import validate_jwt_length
            from src.app.core.security.session_manager import session_manager as global_session_manager

            if not validate_jwt_length(access_token):
                decoded_token = {}
            else:
                decoded_token = jwt.decode(access_token, options={"verify_signature": False})
            session_id = decoded_token.get("jti", access_token[:32])

            device_info_str = json.dumps({"user_agent": "Passkey Login", "ip": "Unknown"})

            await global_session_manager.register_session(
                user_id=user["id"],
                session_id=session_id,
                device_info=device_info_str,
            )
        except Exception as e:
            logger.warning("session_registration_failed", error=str(e))

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }


passkey_service = PasskeyService()
