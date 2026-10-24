"""Passkey service for WebAuthn registration and authentication.

Handles:
- Challenge generation
- Registration verification
- Authentication challenge generation
- Authentication assertion verification
- Credential management
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Request
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
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger
from src.app.core.principal import AuthenticatedUser
from src.app.core.utils.ip_utils import get_client_ip
from src.app.core.utils.lua_manager import get_lua_manager
from src.app.core.utils.user_utils import get_user_attr
from src.app.modules.auth.crud import crud_passkey_credentials
from src.app.modules.auth.schemas import PasskeyCredentialCreate
from src.app.modules.auth.services.tokens import TokenIssuer
from src.app.shared.exceptions import ModelNotFoundError, ServiceUnavailableError, ValidationError

logger = get_logger(__name__)


# Constants
CHALLENGE_TTL = 300  # 5 minutes
AUTH_CHALLENGE_TTL = 300  # 5 minutes for authentication challenges


def _get_rp_id() -> str:
    """Get the relying party ID based on environment."""
    if settings.env.ENVIRONMENT == EnvironmentOption.LOCAL:
        return "localhost"
    # Extract domain from FRONTEND_URL (e.g., https://example.com -> example.com)
    from urllib.parse import urlparse

    parsed = urlparse(settings.app.FRONTEND_URL)
    # M9: rp_id must be a bare hostname — WebAuthn never includes a port in
    # the RP ID, so netloc (which carries :port) breaks non-default ports.
    return parsed.hostname or "localhost"


RP_NAME = settings.app.APP_NAME


class PasskeyService:
    """Service for handling passkey operations."""

    async def generate_registration_challenge(
        self,
        db: AsyncSession,
        user: dict[str, Any] | AuthenticatedUser,
        device_type: str,
        redis: Redis | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Generate WebAuthn registration options (challenge).

        Args:
            db: Database session.
            user: User dictionary (from get_current_user).
            device_type: User-provided device name.
            redis: Redis client for storing challenge.

        Returns:
            tuple: (WebAuthn registration options, registration_id)

        Raises:
            ValidationError: If user has reached passkey limit.
        """
        import uuid

        user_id = get_user_attr(user, "id")
        registration_id = str(uuid.uuid4())

        # 1. Check passkey limit
        if not await crud_passkey_credentials.can_create(db=db, user_id=user_id):
            raise ValidationError(
                f"Maximum number of passkeys ({crud_passkey_credentials.MAX_PASSKEYS_PER_USER}) reached."
            )

        # 2. Get existing credentials to exclude
        existing_creds = await self._get_existing_credentials(db, user_id)

        # 3. Generate registration options
        options: PublicKeyCredentialCreationOptions = generate_registration_options(
            rp_id=_get_rp_id(),
            rp_name=RP_NAME,
            user_id=str(user_id).encode(),
            user_name=get_user_attr(user, "email"),
            user_display_name=get_user_attr(user, "name") or get_user_attr(user, "username"),
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                # BUG FIX: CROSS_PLATFORM was hardcoded, locking out platform authenticators
                # (Touch ID, Face ID, Windows Hello). Removing the constraint allows users
                # to register any authenticator — platform or cross-platform.
                authenticator_attachment=None,
                # Run-3: enrollment now sits behind step-up re-authentication,
                # so the ceremony can also demand user verification. PREFERRED
                # let a stolen session enrol a credential without ever proving
                # local presence (UV) or possession (a PIN/biometric).
                user_verification=UserVerificationRequirement.REQUIRED,
                resident_key=ResidentKeyRequirement.PREFERRED,
            ),
            exclude_credentials=existing_creds,
        )

        # 4. Store challenge in Redis (fail-closed if Redis unavailable)
        if not redis:
            raise ServiceUnavailableError("Passkey registration is temporarily unavailable.")
        await self._store_challenge(redis, registration_id, options.challenge, device_type, user_id)

        result: dict[str, Any] = json_loads(options_to_json(options))
        return result, registration_id

    async def verify_registration(
        self,
        db: AsyncSession,
        user: dict[str, Any] | AuthenticatedUser,
        credential: dict[str, Any],
        registration_id: str,
        redis: Redis | None = None,
    ) -> dict[str, Any]:
        """Verify WebAuthn registration response and store credential.

        Args:
            db: Database session.
            user: User dictionary.
            credential: The WebAuthn response from the client.
            registration_id: The ID of the registration challenge.
            redis: Redis client.

        Returns:
            dict: Created credential information.

        Raises:
            ValidationError: If verification fails or challenge expired.
        """
        user_id = get_user_attr(user, "id")

        # 1. Retrieve stored challenge
        stored_data = await self._get_stored_challenge(redis, registration_id) if redis else None
        if not stored_data:
            raise ValidationError("Registration challenge expired or invalid.")

        if stored_data["user_id"] != user_id:
            raise ValidationError("Registration challenge belongs to another user.")

        challenge = stored_data["challenge"]
        device_type = stored_data["device_type"]

        # 2. Verify registration response
        try:
            from webauthn.helpers import parse_registration_credential_json

            # Parse dict into RegistrationCredential (handles snake_case conversion and base64 decoding)
            parsed_credential = parse_registration_credential_json(credential)

            # §9: the webauthn verification is SYNCHRONOUS crypto (CBOR
            # parsing + signature check) and used to run inline on the event
            # loop. Offloaded like this same file already does for
            # `encryption.decrypt_credential` at the credential-read site --
            # the established pattern here, not a new one.
            verification = await asyncio.to_thread(
                self._verify_with_webauthn,
                credential=parsed_credential,
                expected_challenge=challenge,
                expected_origin=self._get_origin(),
                expected_rp_id=_get_rp_id(),
            )
        except Exception as e:
            logger.warning("Passkey verification failed", error=e, user_id=user_id)
            raise ValidationError("Passkey verification failed.")

        # 3. Encrypt sensitive data
        import hashlib

        from src.app.core.security.encryption import get_encryption

        encryption = get_encryption()
        encrypted_cred_id = encryption.encrypt_credential(verification.credential_id)
        encrypted_public_key = encryption.encrypt_credential(verification.credential_public_key)

        # Compute SHA256 hash of decrypted credential_id for O(log n) lookup
        credential_id_hash = hashlib.sha256(verification.credential_id).hexdigest()

        # 4. Store credential in DB (tenant stamped from the registering
        # user — fail closed: an unscoped credential would be invisible to
        # every tenant RLS context, i.e. a self-inflicted lockout).
        owner_tenant = get_user_attr(user, "tenant_id")
        if owner_tenant is None:
            raise ValidationError("Cannot register passkey: account has no tenant assignment.")
        cred_in = PasskeyCredentialCreate(
            user_id=user_id,
            tenant_id=owner_tenant,
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
        from src.app.modules.users import UserDirectory

        tenant = get_current_tenant()
        if tenant:
            member = await UserDirectory(db).member(id=user_id)
            if member and member.tenant_id != tenant.id:
                raise ModelNotFoundError("PasskeyCredential", user_id, message="Passkey credentials not found.")

        return await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)

    async def list_credentials_page(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        """List ONE PAGE of a user's passkey credentials.

        The tenant check is the same one ``list_credentials`` performs:
        a user who is not a member of the current tenant gets a
        ``ModelNotFoundError`` rather than an empty page, so the
        cross-tenant caller learns nothing from the difference between
        "no credentials" and "not your credentials".

        Unlike ``list_credentials``, this does not load the user's whole
        credential set and slice it in RAM. The page is selected in the
        database and the total comes from the same round trip, so the
        read cost tracks the page size rather than the size of the
        user's whole set.

        Args:
            db: The request-scoped session.
            user_id: The user whose credentials are listed.
            offset: The row offset of the requested page.
            limit: The maximum number of rows in the page.

        Returns:
            ``{"data": [...], "total_count": N}`` — the page's rows and
            the total number of rows for the user.
        """
        from src.app.core.tenancy.context import get_current_tenant
        from src.app.modules.users import UserDirectory

        tenant = get_current_tenant()
        if tenant:
            member = await UserDirectory(db).member(id=user_id)
            if member and member.tenant_id != tenant.id:
                raise ModelNotFoundError("PasskeyCredential", user_id, message="Passkey credentials not found.")

        return await crud_passkey_credentials.get_page_by_user(
            db=db,
            user_id=user_id,
            offset=offset,
            limit=limit,
        )

    async def delete_credential(self, db: AsyncSession, user_id: int, credential_id: int) -> None:
        """Delete a passkey credential."""
        from src.app.core.tenancy.context import get_current_tenant
        from src.app.modules.users import UserDirectory

        tenant = get_current_tenant()
        if tenant:
            member = await UserDirectory(db).member(id=user_id)
            if member and member.tenant_id != tenant.id:
                raise ModelNotFoundError("PasskeyCredential", credential_id, message="Passkey credential not found.")

        cred = await crud_passkey_credentials.get_credential(db=db, id=credential_id)
        # Item 8: deliberately NOT the ownership seam — a wrong-user
        # credential id stays a soft-404 (ModelNotFoundError), never 403, so
        # credential existence is not enumerable across users.
        if not cred or cred.user_id != user_id:
            raise ModelNotFoundError("PasskeyCredential", credential_id, message="Passkey credential not found.")

        await crud_passkey_credentials.delete_credential(db=db, db_object=cred)

    # ============================================================================
    # Authentication Methods (T079, T080)
    # ============================================================================

    async def generate_authentication_challenge(
        self,
        redis: Redis | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Generate WebAuthn authentication options (challenge) for login.

        P2-A: Usernameless (discoverable-credential) flow. The former email
        hint was removed: returning per-email `allowCredentials` let
        unauthenticated callers distinguish accounts with registered passkeys
        (list populated) from unknown or credential-less accounts (list
        omitted) — an account-enumeration oracle. Options now never carry
        `allowCredentials`; verification resolves the user from the asserted
        credential ID server-side (see `verify_authentication`).

        Args:
            redis: Redis client for storing challenge.

        Returns:
            tuple: (WebAuthn authentication options dict, challenge_id for verification).
        """
        import uuid

        # 1. Generate authentication options without allowCredentials
        # (usernameless flow — the authenticator offers discoverable credentials)
        options: PublicKeyCredentialRequestOptions = generate_authentication_options(
            rp_id=_get_rp_id(),
            user_verification=UserVerificationRequirement.REQUIRED,  # M3: 2FA semantics
            allow_credentials=None,
        )

        # 2. Store challenge in Redis with unique challenge_id (fail-closed if Redis unavailable)
        if not redis:
            raise ServiceUnavailableError("Passkey authentication is temporarily unavailable.")

        challenge_id = str(uuid.uuid4())
        await self._store_auth_challenge(redis, challenge_id, options.challenge)

        result: dict[str, Any] = json_loads(options_to_json(options))
        return result, challenge_id

    async def verify_authentication(
        self,
        db: AsyncSession,
        redis: Redis | None = None,
        credential: dict[str, Any] | None = None,
        challenge_id: str | None = None,
        request: Request | None = None,
        response: Any = None,
    ) -> dict[str, Any]:
        """Verify WebAuthn authentication response and return JWT tokens.

        Args:
            db: Database session.
            redis: Redis client.
            credential: The WebAuthn assertion response from the client.
            challenge_id: The challenge_id returned from generate_authentication_challenge.
            request: Optional FastAPI request; supplies client IP/User-Agent
                for the login audit row.
            response: Optional FastAPI response; when given, the refresh
                cookie is written by the issuance seam.
        Returns:
            dict: Access-token response data; the refresh token is delivered by the Issuance cookie seam.

        Raises:
            ValidationError: If verification fails, challenge expired, or replay attempt.
        """
        if not credential:
            logger.debug("passkey verify failed: missing credential")
            raise ValidationError("Invalid passkey assertion.")

        if not challenge_id:
            logger.debug("passkey verify failed: missing challenge_id")
            raise ValidationError("Invalid passkey assertion.")

        # 1. Retrieve stored challenge
        stored_challenge = await self._get_stored_auth_challenge(redis, challenge_id) if redis else None
        if not stored_challenge:
            logger.debug("passkey verify failed: challenge expired/invalid")
            raise ValidationError("Invalid passkey assertion.")

        # 2. Find credential in database by raw ID
        raw_id = credential.get("rawId") or credential.get("id")
        if not raw_id:
            logger.debug("passkey verify failed: missing credential id")
            raise ValidationError("Invalid passkey assertion.")
        db_credential = await self._find_credential_by_id(db, raw_id)
        if not db_credential:
            logger.debug("passkey verify failed: credential not found")
            raise ValidationError("Invalid passkey assertion.")

        # 3. Verify authentication response
        try:
            # §9: synchronous crypto -- off the event loop (see the
            # registration site above).
            verification = await asyncio.to_thread(
                self._verify_authentication_response,
                credential=credential,
                expected_challenge=stored_challenge,
                expected_origin=self._get_origin(),
                expected_rp_id=_get_rp_id(),
                db_credential=db_credential,
            )
        except Exception as e:
            logger.warning("Passkey authentication failed", error=e)
            # P1 FIX (code-review): collapse to a generic message — distinct
            # crypto/challenge/counter errors were an enumeration oracle.
            raise ValidationError("Invalid passkey assertion.")

        # 4. Check counter for replay prevention
        new_count = verification.new_sign_count
        # Per WebAuthn spec §6.1, a new_count of 0 means the authenticator
        # does not support counters at all (it always returns 0). In that case we must
        # NOT treat it as a replay attack, even if stored counter is also 0.
        # Only flag replays when the new count is non-zero AND it does not advance
        # past the stored counter (i.e. new_count <= stored counter).
        if new_count > 0 and new_count <= db_credential.counter:
            logger.warning(
                f"Possible replay attack for credential {db_credential.id}: "
                f"stored counter={db_credential.counter}, received={new_count}"
            )
            raise ValidationError("Invalid passkey assertion.")

        # 5. Update counter and last_used_at
        await self._update_credential_counter(db, db_credential, new_count)

        user = await self._get_user_for_credential(db, db_credential)
        if not user:
            logger.debug("passkey verify failed: user not found for credential")
            raise ValidationError("Invalid passkey assertion.")

        # S4 FIX: Check MFA before issuing tokens (mirrors auth login flow)
        from src.app.modules.auth.services.mfa import get_mfa_service

        mfa_service = get_mfa_service()
        # P1: bind the MFA pending token to the client IP like the
        # password/SSO/magic-link doors so IP-grace enforcement applies.
        mfa_response = await mfa_service.challenge_if_required(
            user, db, redis=redis, ip_address=get_client_ip(request) if request is not None else None
        )
        if mfa_response is not None:
            return mfa_response.model_dump()

        tokens = await self._generate_auth_tokens(user, request=request, response=response)

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

        concurrency_limit = settings.r1_unified.PASSKEY_DECRYPT_CONCURRENCY_LIMIT
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def decrypt_one(cred_id: bytes, transports: Any | None) -> PublicKeyCredentialDescriptor | None:
            async with semaphore:
                try:
                    decrypted_id = await asyncio.to_thread(encryption.decrypt_credential, cred_id)
                    logger.debug("passkey_credential_decrypted", user_identifier=user_identifier)
                    return PublicKeyCredentialDescriptor(
                        type=PublicKeyCredentialType.PUBLIC_KEY,
                        id=decrypted_id,
                        transports=transports,
                    )
                except Exception as e:
                    logger.debug("passkey_credential_decrypt_failed", user_identifier=user_identifier, error=str(e))
                    return None

        # Decrypt all credentials in parallel
        results = await asyncio.gather(*[decrypt_one(cid, tr) for cid, tr in credentials_data])

        # Filter out None values
        valid_results = [r for r in results if r is not None]

        # Log metrics
        elapsed_seconds = time.perf_counter() - start_time
        logger.info(
            "passkey_credentials_decrypted",
            decrypted=len(valid_results),
            total=len(credentials_data),
            elapsed_ms=round(elapsed_seconds * 1000, 2),
            user_identifier=user_identifier,
        )
        record_passkey_query(elapsed_seconds)

        return valid_results

    async def _get_existing_credentials(self, db: AsyncSession, user_id: int) -> list[Any]:
        """Get existing credential IDs for exclusion with parallel decryption."""
        from sqlalchemy import select

        from src.app.core.config import settings
        from src.app.modules.auth.models import PasskeyCredential

        # P3-2 Optimization: Column-specific SELECT for credential_id only

        creds_to_decrypt: list[tuple[bytes, list[str] | None]]
        if settings.r1_unified.PASSKEY_USE_COLUMN_SELECT:
            stmt = (
                select(PasskeyCredential.credential_id)
                .where(PasskeyCredential.user_id == user_id)
                .order_by(PasskeyCredential.created_at.desc())
                .limit(settings.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER)
            )
            result = await db.execute(stmt)
            # Format: list of (id, None)
            creds_to_decrypt = [(row[0], None) for row in result.fetchall()]
        else:
            # Fallback path
            creds = await crud_passkey_credentials.get_by_user(db=db, user_id=user_id)
            if len(creds) > settings.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER:
                creds = creds[: settings.r1_unified.PASSKEY_MAX_CREDENTIALS_PER_USER]

            # Format: list of (id, transports)
            creds_to_decrypt = [(c.credential_id, c.transports if c.transports else None) for c in creds]

        # Use shared helper
        return await self._decrypt_credentials_parallel(creds_to_decrypt, user_id)

    async def _store_challenge(
        self, redis: Redis, registration_id: str, challenge: bytes, device_type: str, user_id: int
    ) -> None:
        """Store registration challenge in Redis."""
        key = f"passkey:reg_challenge:{registration_id}"
        data = {
            "challenge": bytes_to_base64url(challenge),
            "device_type": device_type,
            "user_id": user_id,
        }
        await redis.setex(key, CHALLENGE_TTL, json_dumps(data, as_str=True))

    async def _get_stored_challenge(self, redis: Redis, registration_id: str) -> dict[str, Any] | None:
        """Retrieve and atomically delete registration challenge from Redis."""
        key = f"passkey:reg_challenge:{registration_id}"
        # Atomic get and delete to prevent race conditions
        data = await get_lua_manager().execute(redis, "get_delete", keys=[key])
        if not data:
            return None

        parsed: dict[str, Any] = json_loads(data)
        # Convert challenge back to bytes (base64url decoded) from stored string
        # Note: webauthn expects bytes for expected_challenge
        from webauthn.helpers import base64url_to_bytes

        parsed["challenge"] = base64url_to_bytes(parsed["challenge"])
        return parsed

    async def _delete_challenge(self, redis: Redis, registration_id: str) -> None:
        """Delete registration challenge from Redis."""
        key = f"passkey:reg_challenge:{registration_id}"
        await redis.delete(key)

    async def _store_credential(self, db: AsyncSession, cred_in: PasskeyCredentialCreate) -> dict[str, Any]:
        """Store new credential in database."""
        result: dict[str, Any] | None = cast(
            dict[str, Any] | None, await crud_passkey_credentials.create(db=db, object=cred_in)
        )
        # FastCRUD.create returns None when no schema_to_select per its
        # overload; here INSERT ... RETURNING always yields the row, so
        # None is a contract violation — fail loudly.
        if result is None:
            raise RuntimeError("PasskeyCredential insert returned no row")
        return {
            "id": result["id"],
            "device_type": result["device_type"],
            "created_at": result["created_at"],
            "last_used_at": result.get("last_used_at"),
            "transports": result.get("transports"),
        }

    def _get_origin(self) -> str:
        """Get expected origin based on configured frontend URL."""
        import urllib.parse

        parsed = urllib.parse.urlparse(str(settings.app.FRONTEND_URL))
        return f"{parsed.scheme}://{parsed.netloc}"

    async def _count_user_passkeys(self, db: AsyncSession, user_id: int) -> int:
        """Count user passkeys."""
        return await crud_passkey_credentials.count_by_user(db=db, user_id=user_id)

    def _verify_with_webauthn(self, **kwargs) -> Any:
        """Wrap webauthn verification for easier mocking."""
        return verify_registration_response(**kwargs)

    # ============================================================================
    # Authentication Helpers (T079, T080)
    # ============================================================================

    async def _store_auth_challenge(self, redis: Redis, challenge_id: str, challenge: bytes) -> None:
        """Store authentication challenge in Redis, keyed by unique challenge_id."""
        key = f"passkey:auth_challenge:{challenge_id}"
        await redis.setex(key, AUTH_CHALLENGE_TTL, bytes_to_base64url(challenge))

    async def _get_stored_auth_challenge(self, redis: Redis, challenge_id: str) -> bytes | None:
        """Retrieve and atomically delete authentication challenge from Redis."""
        key = f"passkey:auth_challenge:{challenge_id}"
        # Atomic get and delete to prevent race conditions
        data = await get_lua_manager().execute(redis, "get_delete", keys=[key])
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
        from src.app.modules.auth.models import PasskeyCredential

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
        # M3: enforce UV=REQUIRED on the assertion to match the challenge;
        # makes passkey login a true two-factor flow.
        return verify_authentication_response(
            credential=parsed_credential,
            expected_challenge=expected_challenge,
            expected_origin=expected_origin,
            expected_rp_id=expected_rp_id,
            credential_public_key=credential_public_key,
            credential_current_sign_count=db_credential.counter,
            require_user_verification=True,
        )

    async def _update_credential_counter(self, db: AsyncSession, credential: Any, new_counter: int) -> None:
        """Update credential counter and last_used_at timestamp."""
        credential.counter = new_counter
        credential.last_used_at = datetime.now(UTC)
        await db.commit()

    async def _get_user_for_credential(self, db: AsyncSession, credential: Any) -> dict[str, Any] | None:
        """Get the user associated with a passkey credential with tier eager-loaded."""
        from src.app.modules.users import UserDirectory

        # Tier-eager auth payload via the directory (active-gated +
        # tenant-scoped like every other login door); dumped to dict at
        # the issuance boundary below.
        member = await UserDirectory(db).active(id=credential.user_id)
        if member is None:
            return None
        return member.model_dump(mode="json")

    async def _generate_auth_tokens(
        self, user: dict, request: Request | None = None, response: Any = None
    ) -> dict[str, Any]:
        """Generate JWT authentication tokens and register the session.

        Delegates to the shared TokenIssuer seam ("Passkey Login" device
        label). The optional request supplies client IP/User-Agent for the
        login audit row; without it those fields stay null. The refresh
        cookie is written by the seam when response is given.
        """
        issued = await TokenIssuer.issue_session(
            user,
            channel="passkey",
            request=request,
            response=response,
        )
        return issued.access_response()


passkey_service = PasskeyService()


def get_passkey_service() -> PasskeyService:
    """Get the singleton PasskeyService instance."""
    return passkey_service
