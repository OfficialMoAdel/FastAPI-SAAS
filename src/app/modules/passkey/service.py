"""Passkey service for WebAuthn registration and authentication.

Handles:
- Challenge generation
- Registration verification
- Authentication challenge generation
- Authentication assertion verification
- Credential management
- Limit enforcement
"""

import logging
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
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialRequestOptions,
    PublicKeyCredentialType,
    UserVerificationRequirement,
)

from src.app.core.config import EnvironmentOption, settings
from src.app.core.exceptions.http_exceptions import BadRequestException, NotFoundException
from src.app.core.json_utils import json_dumps, json_loads
from src.app.modules.passkey.crud import crud_passkey_credentials
from src.app.modules.passkey.schemas import PasskeyCredentialCreate

logger = logging.getLogger(__name__)

# Constants
CHALLENGE_TTL = 300  # 5 minutes
AUTH_CHALLENGE_TTL = 300  # 5 minutes for authentication challenges
RP_ID = "localhost" if settings.ENVIRONMENT == EnvironmentOption.LOCAL else settings.DOMAIN_NAME
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
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_id=str(user_id).encode(),
            user_name=user["email"],
            user_display_name=user.get("name") or user["username"],
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
                user_verification=UserVerificationRequirement.PREFERRED,
                resident_key=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=existing_creds,
        )

        # 4. Store challenge in Redis
        if redis:
            await self._store_challenge(redis, user_id, options.challenge, device_type)

        return json_loads(options_to_json(options))

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
                expected_rp_id=RP_ID,
            )
        except Exception as e:
            logger.warning(f"Passkey verification failed for user {user_id}: {e}")
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

        # 5. Cleanup
        if redis:
            await self._delete_challenge(redis, user_id)

        return created_cred

    async def list_credentials(self, db: AsyncSession, user_id: int) -> list[Any]:
        """List all passkey credentials for a user."""
        return await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)

    async def delete_credential(self, db: AsyncSession, user_id: int, credential_id: int) -> None:
        """Delete a passkey credential."""
        cred = await crud_passkey_credentials.get(db=db, id=credential_id)
        if not cred or cred.user_id != user_id:
            raise NotFoundException(detail="Passkey credential not found.")

        await crud_passkey_credentials.delete(db=db, db_object=cred)

    # ============================================================================
    # Authentication Methods (T079, T080)
    # ============================================================================

    async def generate_authentication_challenge(
        self,
        db: AsyncSession,
        redis: Redis | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        """Generate WebAuthn authentication options (challenge) for login.

        Args:
            db: Database session.
            redis: Redis client for storing challenge.
            email: Optional email hint to filter allowed credentials (for discoverable credentials).

        Returns:
            dict: WebAuthn authentication options (PublicKeyCredentialRequestOptions).
        """
        # 1. Get allowed credentials if email provided
        allowed_credentials: list[PublicKeyCredentialDescriptor] = []
        if email:
            allowed_credentials = await self._get_credentials_for_email(db, email)

        # 2. Generate authentication options
        options: PublicKeyCredentialRequestOptions = generate_authentication_options(
            rp_id=RP_ID,
            user_verification=UserVerificationRequirement.PREFERRED,
            allow_credentials=allowed_credentials if allowed_credentials else None,
        )

        # 3. Store challenge in Redis for verification
        if redis:
            await self._store_auth_challenge(redis, options.challenge)

        return json_loads(options_to_json(options))

    async def verify_authentication(
        self,
        db: AsyncSession,
        redis: Redis | None = None,
        credential: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify WebAuthn authentication response and return JWT tokens.

        Args:
            db: Database session.
            redis: Redis client.
            credential: The WebAuthn assertion response from the client.

        Returns:
            dict: Authentication tokens (access_token, refresh_token, etc.).

        Raises:
            BadRequestException: If verification fails, challenge expired, or replay attempt.
        """
        if not credential:
            raise BadRequestException(detail="Credential is required.")

        # 1. Retrieve stored challenge
        stored_challenge = await self._get_stored_auth_challenge(redis) if redis else None
        if not stored_challenge:
            raise BadRequestException(detail="Authentication challenge expired or invalid.")

        # 2. Find credential in database by raw ID
        raw_id = credential.get("rawId") or credential.get("id")
        db_credential = await self._find_credential_by_id(db, raw_id)
        if not db_credential:
            raise BadRequestException(detail="Credential not found.")

        # 3. Verify authentication response
        try:
            verification = self._verify_authentication_response(
                credential=credential,
                expected_challenge=stored_challenge,
                expected_origin=self._get_origin(),
                expected_rp_id=RP_ID,
                db_credential=db_credential,
            )
        except Exception as e:
            logger.warning(f"Passkey authentication failed: {e}")
            raise BadRequestException(detail="Passkey authentication failed.")

        # 4. Check counter for replay prevention
        new_count = verification.new_sign_count
        if new_count <= db_credential.counter:
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

        # 7. Cleanup
        if redis:
            await self._delete_auth_challenge(redis)

        return tokens

    # ============================================================================
    # Internal Helpers
    # ============================================================================

    async def _get_existing_credentials(self, db: AsyncSession, user_id: int) -> list[Any]:
        """Get existing credential IDs for exclusion."""
        creds = await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)
        from src.app.core.security.encryption import get_encryption

        encryption = get_encryption()

        # Return decrypted credential IDs for webauthn exclusion
        existing = []
        for cred in creds:
            try:
                decrypted_id = encryption.decrypt_credential(cred.credential_id)
                existing.append(PublicKeyCredentialDescriptor(type=PublicKeyCredentialType.PUBLIC_KEY, id=decrypted_id))
            except Exception:
                # Skip invalid/corrupt credentials
                continue
        return existing

    async def _store_challenge(self, redis: Redis, user_id: int, challenge: bytes, device_type: str) -> None:
        """Store registration challenge in Redis."""
        key = f"passkey:challenge:{user_id}"
        data = {
            "challenge": bytes_to_base64url(challenge),
            "device_type": device_type,
        }
        await redis.setex(key, CHALLENGE_TTL, json_dumps(data, as_str=True))

    async def _get_stored_challenge(self, redis: Redis, user_id: int) -> dict[str, Any] | None:
        """Retrieve registration challenge from Redis."""
        key = f"passkey:challenge:{user_id}"
        data = await redis.get(key)
        if not data:
            return None

        parsed = json_loads(data)
        # Convert challenge back to bytes (base64url decoded) from stored string
        # Note: webauthn expects bytes for expected_challenge
        from webauthn.helpers import base64url_to_bytes

        parsed["challenge"] = base64url_to_bytes(parsed["challenge"])
        return parsed

    async def _delete_challenge(self, redis: Redis, user_id: int) -> None:
        """Delete registration challenge from Redis."""
        key = f"passkey:challenge:{user_id}"
        await redis.delete(key)

    async def _store_credential(self, db: AsyncSession, cred_in: PasskeyCredentialCreate) -> Any:
        """Store new credential in database."""
        from src.app.modules.passkey.schemas import PasskeyCredentialResponse

        return await crud_passkey_credentials.create(db=db, object=cred_in, schema_to_select=PasskeyCredentialResponse)

    def _get_origin(self) -> str:
        """Get expected origin based on environment."""
        if settings.ENVIRONMENT == EnvironmentOption.LOCAL:
            return "http://localhost:3000"  # Default frontend port
        # In production, use configured frontend URL
        return settings.FRONTEND_URL if hasattr(settings, "FRONTEND_URL") else f"https://{settings.DOMAIN_NAME}"

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
        """Get passkey credentials for a user by email."""
        from src.app.core.security.encryption import get_encryption
        from src.app.modules.users.crud import crud_users

        # Find user by email
        user = await crud_users.get(db=db, email=email)
        if not user:
            return []

        # Get credentials for the user
        creds = await crud_passkey_credentials.get_by_user(db=db, user_id=user.id)
        encryption = get_encryption()

        result = []
        for cred in creds:
            try:
                decrypted_id = encryption.decrypt_credential(cred.credential_id)
                transports = cred.transports if cred.transports else None
                result.append(
                    PublicKeyCredentialDescriptor(
                        type=PublicKeyCredentialType.PUBLIC_KEY,
                        id=decrypted_id,
                        transports=transports,
                    )
                )
            except Exception:
                continue
        return result

    async def _store_auth_challenge(self, redis: Redis, challenge: bytes) -> None:
        """Store authentication challenge in Redis."""
        key = "passkey:auth_challenge"
        await redis.setex(key, AUTH_CHALLENGE_TTL, bytes_to_base64url(challenge))

    async def _get_stored_auth_challenge(self, redis: Redis) -> bytes | None:
        """Retrieve authentication challenge from Redis."""
        key = "passkey:auth_challenge"
        data = await redis.get(key)
        if not data:
            return None
        return base64url_to_bytes(data)

    async def _delete_auth_challenge(self, redis: Redis) -> None:
        """Delete authentication challenge from Redis."""
        key = "passkey:auth_challenge"
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
        stmt = select(PasskeyCredential).where(
            PasskeyCredential.credential_id_hash == target_hash
        )
        result = await db.execute(stmt)
        cred = result.scalar_one_or_none()

        if cred is not None:
            # Defense in depth: verify the hash matches decrypted credential
            encryption = get_encryption()
            try:
                decrypted_id = encryption.decrypt_credential(cred.credential_id)
                if decrypted_id == credential_id_bytes:
                    return cred
            except Exception:
                pass  # Hash collision or decryption failure, fall through to scan

        # Fallback path: O(n) scan for credentials without hash (migration period)
        # This will be removed after backfill is complete
        encryption = get_encryption()
        result = await db.execute(
            select(PasskeyCredential).where(
                PasskeyCredential.credential_id_hash.is_(None)
            )
        )
        unhashed_creds = result.scalars().all()

        for cred in unhashed_creds:
            try:
                decrypted_id = encryption.decrypt_credential(cred.credential_id)
                if decrypted_id == credential_id_bytes:
                    return cred
            except Exception:
                continue

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

    async def _get_user_for_credential(self, db: AsyncSession, credential: Any) -> dict | None:
        """Get the user associated with a passkey credential with tier eager-loaded."""
        from src.app.modules.users.crud import crud_users

        return await crud_users.get_user_with_tier_eager(db=db, id=credential.user_id)

    async def _generate_auth_tokens(self, user: dict) -> dict[str, Any]:
        """Generate JWT authentication tokens for a user with tier info for rate limiting."""
        from src.app.core.security import create_access_token, create_refresh_token

        # Include tier info in JWT claims for Zero-DB rate limiting
        tier = user.get("tier")
        tier_name = tier.get("name") if tier else None
        token_data = {
            "sub": user["username"],
            "tier_id": user.get("tier_id"),
            "tier_name": tier_name,
        }

        access_token = await create_access_token(data=token_data)
        refresh_token = await create_refresh_token(data=token_data)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }


passkey_service = PasskeyService()
