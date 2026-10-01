"""Authentication and identity schemas.

Consolidated DTOs for:
- Token & Session management
- Password login, change, reset, and email verification
- OAuth/OIDC Single Sign-On (SSO) and provider settings
- WebAuthn passkey registration and assertion
- Magic link passwordless authentication
- Multi-factor authentication (TOTP and backup codes)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.app.core.schemas import Token, TokenBlacklistBase, TokenBlacklistCreate, TokenBlacklistRead, TokenData
from src.app.modules._base import BaseCreate, BaseList, BaseRead, BaseUpdate
from src.app.shared.exceptions import ValidationError as DomainValidationError

# ============================================================================
# Token & Session Schemas
# ============================================================================


class AuthTokenResponse(BaseModel):
    """Access-only authentication response for SSO service callers."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration time in seconds")


class LoginRequest(BaseModel):
    """Login request schema (for documentation)."""

    username: str
    password: str


class LoginTokenResponse(BaseModel):
    """Login success response without a JSON refresh-token field."""

    access_token: str
    token_type: str = "bearer"


class LoginMFAResponse(BaseModel):
    """Login MFA required response schema."""

    mfa_required: bool = True
    mfa_token: str
    expires_in: int
    message: str


class RefreshTokenResponse(BaseModel):
    """Refresh token response schema."""

    access_token: str
    token_type: str = "bearer"


class LogoutResponse(BaseModel):
    """Logout response schema.

    ``session_revoked`` distinguishes a full logout (server-side session
    entry invalidated) from a partial one (tokens blacklisted only). P1-2.
    """

    message: str = "Logged out successfully"
    session_revoked: bool = False


class SessionInfo(BaseModel):
    """Session information schema."""

    session_id: str | None = None
    id: str | None = None
    device_label: str = "Unknown"
    created_at: int
    last_seen_at: int | None = None
    last_accessed_at: int | None = None
    is_current: bool = False
    user_id: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class SessionListResponse(BaseModel):
    """Session list response schema."""

    sessions: list[SessionInfo]


class SessionCountResponse(BaseModel):
    """Session count response schema."""

    count: int


# ============================================================================
# Password Lifecycle & Email Verification
# ============================================================================


class PasswordChangeRequest(BaseModel):
    """Password change request schema."""

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    """Password reset request schema (forgot password)."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation schema."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class EmailVerificationRequest(BaseModel):
    """Schema for requesting email verification (resend)."""

    email: str = Field(..., max_length=255)


class EmailVerificationConfirm(BaseModel):
    """Schema for confirming email verification."""

    token: str


class MessageResponse(BaseModel):
    """Generic message response schema."""

    message: str


PasswordChangeResponse = MessageResponse
PasswordResetResponse = MessageResponse
EmailVerificationResponse = MessageResponse
ForgotPasswordResponse = MessageResponse


# ============================================================================
# Single Sign-On (SSO) Schemas
# ============================================================================


class SSOProvider(str, Enum):
    GOOGLE = "google"
    GITHUB = "github"
    MICROSOFT = "microsoft"
    APPLE = "apple"


class UserSSOConnectionBase(BaseModel):
    provider: SSOProvider
    provider_user_id: str
    provider_email: str | None = None
    token_expires_at: datetime | None = None


class UserSSOConnectionCreate(BaseCreate):
    provider: SSOProvider
    provider_user_id: str
    provider_email: str | None = None
    token_expires_at: datetime | None = None
    user_id: int
    tenant_id: UUID
    access_token: bytes | None = None
    refresh_token: bytes | None = None


class UserSSOConnectionUpdate(BaseUpdate):
    provider_email: str | None = None
    access_token: bytes | None = None
    refresh_token: bytes | None = None
    token_expires_at: datetime | None = None


class UserSSOConnectionRead(BaseRead):
    provider: SSOProvider
    provider_user_id: str
    provider_email: str | None = None
    token_expires_at: datetime | None = None
    user_id: int
    tenant_id: UUID


class SSOProviderRead(BaseModel):
    name: SSOProvider
    enabled: bool
    display_name: str


class SSOProvidersResponse(BaseModel):
    providers: list[SSOProviderRead]


# SSO Settings Schemas
class SSOSettingsBase(BaseModel):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True


class SSOSettingsCreate(BaseCreate):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True
    client_id: str
    client_secret: str


class SSOSettingsCreateInternal(BaseCreate):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True
    client_id: bytes
    client_secret: bytes


class SSOSettingsUpdate(BaseUpdate):
    client_id: str | None = None
    client_secret: str | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsUpdateInternal(BaseUpdate):
    client_id: bytes | None = None
    client_secret: bytes | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsDelete(BaseUpdate):
    pass


class SSOSettingsRead(BaseRead):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True


class SSOSettingsReadWithSecrets(SSOSettingsRead):
    client_id: str
    client_secret: str


class SSOSettingsList(BaseList[SSOSettingsRead]):
    pass


# ============================================================================
# Passkey (WebAuthn) Schemas
# ============================================================================

# A WebAuthn credential is a small, fixed-shape object: {id, rawId, response,
# type, authenticatorAttachment?, clientExtensionResults?}, where `response` is
# a flat map of base64url strings. 32 entries / depth 6 leave an order of
# magnitude of headroom over that shape while still bounding what a client can
# make the parser walk. The body-limit middleware (settings.app.MAX_BODY_SIZE,
# 10 MiB by default) bounds the BYTES; these two bounds bound the SHAPE — one
# body under the byte cap can still carry a 200k-key dict or a 10k-deep nest,
# and both reach the WebAuthn parser, the log sanitizer and the DB write.
PASSKEY_CREDENTIAL_MAX_ENTRIES = 32
PASSKEY_CREDENTIAL_MAX_DEPTH = 6


def _max_nesting_depth(value: object) -> int:
    """Depth of the deepest container chain inside ``value`` (the value itself is depth 1).

    Iterative, not recursive: this runs on attacker-controlled input, and a
    recursive walk would raise RecursionError on exactly the payload it exists
    to reject.
    """
    deepest = 0
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        # dict.values() and list/tuple are both Iterable; annotating the union
        # is what lets mypy see one container type across the branches.
        children: Iterable[Any]
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list | tuple):
            children = node
        else:
            continue  # a scalar never deepens the chain
        if depth > deepest:
            deepest = depth
        stack.extend((item, depth + 1) for item in children)
    return deepest


def _bound_passkey_credential_depth(credential: dict[str, Any]) -> dict[str, Any]:
    """Reject a credential nested deeper than PASSKEY_CREDENTIAL_MAX_DEPTH.

    Raises the domain ``ValidationError`` (400), NOT a bare ``ValueError``.
    Pydantic only converts ``ValueError`` into a ``ValidationError``, and
    FastAPI's default handler then json-encodes ``errors()`` — which echoes the
    offending ``input``. For a payload deep enough to be rejected here, that
    echo is a 1000+-frame recursive encode, so the request comes back 500
    instead of 4xx (measured: the echo raises RecursionError at nesting ~998).
    The domain error skips pydantic's error machinery entirely and lands on
    this repo's handler, which returns a fixed envelope and never touches the
    payload — a clean 400 at every depth.
    """
    depth = _max_nesting_depth(credential)
    if depth > PASSKEY_CREDENTIAL_MAX_DEPTH:
        raise DomainValidationError(
            f"credential must be at most {PASSKEY_CREDENTIAL_MAX_DEPTH} levels deep, got {depth}"
        )
    return credential


# Shared by the two client-controlled credential fields. The entry cap is a
# core dict constraint (``max_length`` -> ``maxProperties`` in OpenAPI), applied
# before the walk below, so a huge flat dict is rejected without being
# traversed. A wide dict is safe to report as a 422: pydantic attaches it as
# ``input`` but does not recurse while doing so, and the byte cap already caps
# its size.
BoundedPasskeyCredential = Annotated[
    dict[str, Any],
    Field(max_length=PASSKEY_CREDENTIAL_MAX_ENTRIES),
    AfterValidator(_bound_passkey_credential_depth),
]


class StepUpRequest(BaseModel):
    """Step-up credential for factor mutations that take no other body.

    Run-3: kept in the body (never a header) so the password is not captured by
    intermediaries that log request headers.
    """

    password: str = Field(..., min_length=1, max_length=256)


class PasskeyRegistrationRequest(BaseModel):
    device_type: str = Field(..., min_length=1, max_length=100)
    # Run-3 finding auth.mfa.passkey_enrollment_without_reauthentication:
    # enrolling a durable credential requires re-proving possession of a
    # credential a bearer-token thief does not have. Matches the convention
    # disable_mfa / regenerate_backup_codes already use (password in body).
    password: str = Field(..., min_length=1, max_length=256)


class PasskeyRegistrationVerifyRequest(BaseModel):
    # Bounded (entries + depth): see BoundedPasskeyCredential above.
    credential: BoundedPasskeyCredential
    registration_id: str
    # Re-verified at commit time as well as at challenge time, so an
    # intercepted challenge cannot be completed later without the password.
    password: str = Field(..., min_length=1, max_length=256)


class PasskeyRegistrationOptionsResponse(BaseModel):
    publicKey: dict[str, Any]
    registration_id: str


class PasskeyCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    id: int
    device_type: str
    created_at: datetime
    last_used_at: datetime | None = None
    aaguid: str | Any | None = None
    transports: list[str] | None = None


class PasskeyCredentialList(BaseList[PasskeyCredentialResponse]):
    pass


class PasskeyDeleteResponse(BaseModel):
    message: str = Field(..., description="Success message")


class PasskeyLoginVerifyRequest(BaseModel):
    # Bounded (entries + depth): see BoundedPasskeyCredential above.
    credential: BoundedPasskeyCredential
    challenge_id: str


class PasskeyAuthenticationOptionsResponse(BaseModel):
    publicKey: dict[str, Any]
    challenge_id: str


class PasskeyCredentialCreate(BaseCreate):
    user_id: int
    tenant_id: UUID
    credential_id: bytes
    public_key: bytes
    device_type: str = "passkey"
    counter: int = 0
    aaguid: UUID | None = None
    transports: list[str] | None = None
    credential_id_hash: str | None = None


class PasskeyCredentialUpdate(BaseUpdate):
    counter: int | None = None
    last_used_at: datetime | None = None
    device_type: str | None = None


class PasskeyCredentialRead(BaseRead):
    user_id: int
    device_type: str
    counter: int
    aaguid: UUID | None = None
    transports: list[str] | None = None
    created_at: datetime
    last_used_at: datetime | None = None


# ============================================================================
# Magic Link Schemas
# ============================================================================


class MagicLinkSendRequest(BaseModel):
    email: EmailStr


class MagicLinkSendResponse(BaseModel):
    message: str
    may_be_delayed: bool = False


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=64)


class MagicLinkCreate(BaseCreate):
    user_id: int
    token: str = Field(..., max_length=64)
    action: str = Field(..., max_length=50)
    expires_at: datetime
    ip_address: str | None = None
    user_agent: str | None = None


class MagicLinkRead(BaseRead):
    user_id: int
    token: str
    action: str
    expires_at: datetime
    used_at: datetime | None = None
    ip_address: str | None = None
    user_agent: str | None = None


# ============================================================================
# Multi-Factor Authentication (MFA) Schemas
# ============================================================================

_TOTP_TOKEN_PATTERN = re.compile(r"^\d{6}$")
# S3/SEC-10: two layouts exist — legacy 40-bit codes (two 5-char groups,
# still stored on accounts that have not regenerated) and current 80-bit
# codes (four 5-char groups). The optional "BC-" prefix is normalized in by
# the request validators.
_BACKUP_CODE_PATTERN = re.compile(
    r"^(?:BC-)?(?:[A-Z0-9]{5}-[A-Z0-9]{5}|[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5})$"
)


class MFASetupResponse(BaseModel):
    setup_token: str
    otpauth_url: str
    qr_code_base64: str


class MFAEnableRequest(BaseModel):
    setup_token: str
    totp_token: str = Field(..., min_length=6, max_length=6)
    # Run-3: enabling a second factor must require re-authentication, not just
    # a valid session.
    password: str = Field(..., min_length=1, max_length=256)

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token(cls, v: str) -> str:
        if not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")
        return v


class MFAEnableResponse(BaseModel):
    message: str = "MFA enabled successfully"
    backup_codes: list[str]
    backup_codes_remaining: int = 10


class MFAVerifyRequest(BaseModel):
    totp_token: str | None = None
    backup_code: str | None = None

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token(cls, v: str | None) -> str | None:
        if v is not None and not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")
        return v

    @field_validator("backup_code")
    @classmethod
    def validate_backup_code(cls, v: str | None) -> str | None:
        if v is not None:
            v_upper = v.upper()
            if not _BACKUP_CODE_PATTERN.match(v_upper):
                raise ValueError("Invalid backup code format")
            return v_upper
        return v

    def model_post_init(self, __context: Any) -> None:
        """Validate that exactly one of totp_token or backup_code is provided."""
        if self.totp_token is None and self.backup_code is None:
            raise ValueError("Either totp_token or backup_code must be provided")
        if self.totp_token is not None and self.backup_code is not None:
            raise ValueError("Only one of totp_token or backup_code should be provided, not both")


class MFAVerifyResponse(BaseModel):
    valid: bool = True
    message: str = "Verification successful"
    backup_codes_remaining: int | None = None


class MFADisableRequest(BaseModel):
    password: str = Field(..., min_length=8)
    totp_token: str | None = None
    backup_code: str | None = None

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token(cls, v: str | None) -> str | None:
        if v is not None and not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")
        return v

    @field_validator("backup_code")
    @classmethod
    def normalize_backup_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.upper().strip()
        if not _BACKUP_CODE_PATTERN.match(normalized):
            raise ValueError("Invalid backup code format")
        if not normalized.startswith("BC-"):
            normalized = f"BC-{normalized}"
        return normalized

    def model_post_init(self, __context: Any) -> None:
        """Validate that exactly one of totp_token or backup_code is provided."""
        if self.totp_token is None and self.backup_code is None:
            raise ValueError("Either totp_token or backup_code must be provided")
        if self.totp_token is not None and self.backup_code is not None:
            raise ValueError("Only one of totp_token or backup_code should be provided, not both")


class MFAStatusResponse(BaseModel):
    is_enabled: bool
    backup_codes_remaining: int | None = None
    last_used: str | None = None


class MFABackupCodesResponse(BaseModel):
    backup_codes: list[str]
    message: str = "New backup codes generated successfully"


class MFACompleteLoginRequest(BaseModel):
    mfa_token: str = Field(..., min_length=32, max_length=64)
    totp_token: str | None = None
    backup_code: str | None = None

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token(cls, v: str | None) -> str | None:
        if v is not None and not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")
        return v

    @field_validator("backup_code")
    @classmethod
    def normalize_backup_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.upper().strip()
        if not _BACKUP_CODE_PATTERN.match(normalized):
            raise ValueError("Invalid backup code format")
        if not normalized.startswith("BC-"):
            normalized = f"BC-{normalized}"
        return normalized

    def model_post_init(self, __context: Any) -> None:
        """Validate that exactly one of totp_token or backup_code is provided."""
        if self.totp_token is None and self.backup_code is None:
            raise ValueError("Either totp_token or backup_code must be provided")
        if self.totp_token is not None and self.backup_code is not None:
            raise ValueError("Only one of totp_token or backup_code should be provided, not both")


class MFACompleteLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    message: str = "Login successful"


__all__ = [
    # Core Tokens & Session
    "Token",
    "TokenData",
    "TokenBlacklistBase",
    "TokenBlacklistCreate",
    "TokenBlacklistRead",
    "AuthTokenResponse",
    "LoginRequest",
    "LoginTokenResponse",
    "LoginMFAResponse",
    "RefreshTokenResponse",
    "LogoutResponse",
    "SessionInfo",
    "SessionListResponse",
    "SessionCountResponse",
    # Passwords & Email Verification
    "PasswordChangeRequest",
    "PasswordResetRequest",
    "PasswordResetConfirm",
    "EmailVerificationRequest",
    "EmailVerificationConfirm",
    "MessageResponse",
    "PasswordChangeResponse",
    "PasswordResetResponse",
    "EmailVerificationResponse",
    "ForgotPasswordResponse",
    # SSO
    "SSOProvider",
    "UserSSOConnectionBase",
    "UserSSOConnectionCreate",
    "UserSSOConnectionUpdate",
    "UserSSOConnectionRead",
    "SSOProviderRead",
    "SSOProvidersResponse",
    "SSOSettingsBase",
    "SSOSettingsCreate",
    "SSOSettingsCreateInternal",
    "SSOSettingsUpdate",
    "SSOSettingsUpdateInternal",
    "SSOSettingsDelete",
    "SSOSettingsRead",
    "SSOSettingsReadWithSecrets",
    "SSOSettingsList",
    # Passkey
    "BoundedPasskeyCredential",
    "PASSKEY_CREDENTIAL_MAX_ENTRIES",
    "PASSKEY_CREDENTIAL_MAX_DEPTH",
    "PasskeyRegistrationRequest",
    "PasskeyRegistrationVerifyRequest",
    "PasskeyRegistrationOptionsResponse",
    "PasskeyCredentialResponse",
    "PasskeyCredentialList",
    "PasskeyDeleteResponse",
    "PasskeyLoginVerifyRequest",
    "PasskeyAuthenticationOptionsResponse",
    "PasskeyCredentialCreate",
    "PasskeyCredentialUpdate",
    "PasskeyCredentialRead",
    # Magic Link
    "MagicLinkSendRequest",
    "MagicLinkSendResponse",
    "MagicLinkVerifyRequest",
    "MagicLinkCreate",
    "MagicLinkRead",
    # MFA
    "MFASetupResponse",
    "MFAEnableRequest",
    "MFAEnableResponse",
    "MFAVerifyRequest",
    "MFAVerifyResponse",
    "MFADisableRequest",
    "MFAStatusResponse",
    "MFABackupCodesResponse",
    "MFACompleteLoginRequest",
    "MFACompleteLoginResponse",
]
