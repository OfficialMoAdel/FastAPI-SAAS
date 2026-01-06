"""Pydantic schemas for passkey operations.

Task: T070 [US7] Create Pydantic schemas for passkey operations

Schemas for:
- PasskeyCredential CRUD operations
- Registration options and verification
- Credential listing and deletion
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# Request Schemas
# ============================================================================


class PasskeyRegistrationRequest(BaseModel):
    """Request to initiate passkey registration."""

    device_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable device name (e.g., 'Touch ID', 'YubiKey')",
    )


class PasskeyRegistrationVerifyRequest(BaseModel):
    """Request to complete passkey registration with WebAuthn credential."""

    credential: dict[str, Any] = Field(
        ...,
        description="WebAuthn PublicKeyCredential from browser",
    )


# ============================================================================
# Response Schemas
# ============================================================================


class PasskeyRegistrationOptionsResponse(BaseModel):
    """Response containing WebAuthn PublicKeyCredentialCreationOptions."""

    publicKey: dict[str, Any] = Field(
        ...,
        description="WebAuthn PublicKeyCredentialCreationOptions",
    )


class PasskeyCredentialResponse(BaseModel):
    """Response for a single passkey credential."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Credential database ID")
    device_type: str = Field(..., description="Human-readable device name")
    created_at: datetime = Field(..., description="Credential creation timestamp")
    last_used_at: datetime | None = Field(
        None, description="Last successful authentication timestamp"
    )
    transports: list[str] | None = Field(
        None, description="Supported transports (usb, nfc, ble, internal, hybrid)"
    )


class PasskeyCredentialListResponse(BaseModel):
    """Response for listing user's passkey credentials."""

    credentials: list[PasskeyCredentialResponse] = Field(
        default_factory=list,
        description="List of user's passkey credentials",
    )


class PasskeyDeleteResponse(BaseModel):
    """Response for credential deletion."""

    message: str = Field(..., description="Success message")


# ============================================================================
# Authentication Schemas (T081 - Passkey Login)
# ============================================================================


class PasskeyLoginRequest(BaseModel):
    """Request to initiate passkey authentication (optional email hint)."""

    email: str | None = Field(
        None,
        description="Optional email hint for discoverable credentials",
    )


class PasskeyLoginVerifyRequest(BaseModel):
    """Request to complete passkey authentication with WebAuthn assertion."""

    credential: dict[str, Any] = Field(
        ...,
        description="WebAuthn PublicKeyCredential (assertion) from browser",
    )


class PasskeyAuthenticationOptionsResponse(BaseModel):
    """Response containing WebAuthn PublicKeyCredentialRequestOptions."""

    publicKey: dict[str, Any] = Field(
        ...,
        description="WebAuthn PublicKeyCredentialRequestOptions",
    )


class AuthTokenResponse(BaseModel):
    """Response containing authentication tokens."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type (always 'bearer')")
    expires_in: int = Field(..., description="Access token expiry in seconds")


# ============================================================================
# Internal/CRUD Schemas
# ============================================================================


class PasskeyCredentialCreate(BaseModel):
    """Schema for creating a new passkey credential."""

    user_id: int = Field(..., description="User ID")
    credential_id: bytes = Field(..., description="Encrypted WebAuthn credential ID")
    public_key: bytes = Field(..., description="Encrypted COSE public key")
    counter: int = Field(default=0, description="Signature counter")
    device_type: str = Field(..., max_length=100, description="Device name")
    aaguid: UUID | str | None = Field(None, description="Authenticator AAGUID")
    transports: list[str] | None = Field(None, description="Supported transports")


class PasskeyCredentialUpdate(BaseModel):
    """Schema for updating a passkey credential."""

    counter: int | None = Field(None, description="Updated signature counter")
    last_used_at: datetime | None = Field(None, description="Last authentication time")


class PasskeyCredentialRead(BaseModel):
    """Schema for reading a passkey credential from database."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    credential_id: bytes
    public_key: bytes
    counter: int
    device_type: str
    aaguid: str | None = None
    transports: list[str] | None = None
    created_at: datetime
    last_used_at: datetime | None = None
