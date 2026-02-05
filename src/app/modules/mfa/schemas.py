"""MFA (Multi-Factor Authentication) schemas for API request/response validation."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# Pre-compiled regex patterns for performance
_TOTP_TOKEN_PATTERN = re.compile(r"^\d{6}$")
_BACKUP_CODE_PATTERN = re.compile(r"^(BC-)?[A-Z0-9]{5}-[A-Z0-9]{5}$")


# =============================================================================
# MFA Setup Schemas
# =============================================================================


class MFASetupResponse(BaseModel):
    """Response schema for MFA setup initiation.

    Contains TOTP secret and QR code for user to configure their authenticator app.
    """

    totp_secret: Annotated[
        str,
        Field(
            min_length=32,
            max_length=32,
            description="Base32-encoded TOTP secret",
            examples=["JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"],
        ),
    ]
    otpauth_url: Annotated[
        str,
        Field(
            description="OTPAuth URL for manual entry into authenticator apps",
            examples=["otpauth://totp/App:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=App"],
        ),
    ]
    qr_code_base64: Annotated[
        str,
        Field(
            description="Base64-encoded PNG QR code image (data:image/png;base64,...)",
            examples=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."],
        ),
    ]


# =============================================================================
# MFA Enable Schemas
# =============================================================================


class MFAEnableRequest(BaseModel):
    """Request schema for enabling MFA.

    User must provide the TOTP secret they received during setup and verify
    they can generate valid tokens.
    """

    totp_secret: Annotated[
        str,
        Field(
            min_length=32,
            max_length=32,
            description="Base32-encoded TOTP secret from setup",
            examples=["JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"],
        ),
    ]
    totp_token: Annotated[
        str,
        Field(
            min_length=6,
            max_length=6,
            pattern=r"^\d{6}$",
            description="6-digit TOTP code from authenticator app",
            examples=["123456"],
        ),
    ]


class MFAEnableResponse(BaseModel):
    """Response schema for successful MFA enablement.

    Returns backup codes that user must save securely. These codes can be used
    to access the account if the authenticator app is lost.
    """

    message: Annotated[
        str,
        Field(default="MFA enabled successfully", description="Success message", examples=["MFA enabled successfully"]),
    ]
    backup_codes: Annotated[
        list[str],
        Field(
            min_length=10,
            max_length=10,
            description="List of backup codes (format: BC-XXXXX-XXXXX)",
            examples=[["BC-ABCDE-FGHIJ", "BC-KLMNO-PQRST", "BC-UVWXY-Z1234"]],
        ),
    ]
    backup_codes_remaining: Annotated[
        int, Field(ge=0, le=10, description="Number of unused backup codes", examples=[10])
    ]


# =============================================================================
# MFA Verify Schemas
# =============================================================================


class MFAVerifyRequest(BaseModel):
    """Request schema for MFA verification.

    User must provide either a TOTP token or a backup code.
    Exactly one of the fields must be provided.
    """

    totp_token: Annotated[
        str | None,
        Field(
            default=None,
            min_length=6,
            max_length=6,
            pattern=r"^\d{6}$",
            description="6-digit TOTP code from authenticator app",
            examples=["123456"],
        ),
    ]
    backup_code: Annotated[
        str | None,
        Field(
            default=None,
            min_length=11,
            max_length=14,
            description="Backup code (format: BC-XXXXX-XXXXX or XXXXX-XXXXX)",
            examples=["BC-ABCDE-FGHIJ", "ABCDE-FGHIJ"],
        ),
    ]

    @field_validator("backup_code")
    @classmethod
    def normalize_backup_code(cls, v: str | None) -> str | None:
        """Normalize backup code to uppercase and validate format."""
        if v is None:
            return None

        # Convert to uppercase
        normalized = v.upper().strip()

        # Validate format (BC-XXXXX-XXXXX or XXXXX-XXXXX)
        if not _BACKUP_CODE_PATTERN.match(normalized):
            raise ValueError("Invalid backup code format. Expected: BC-XXXXX-XXXXX or XXXXX-XXXXX")

        # Ensure BC- prefix exists
        if not normalized.startswith("BC-"):
            normalized = f"BC-{normalized}"

        return normalized

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token_format(cls, v: str | None) -> str | None:
        """Validate TOTP token format."""
        if v is None:
            return None

        if not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")

        return v

    def model_post_init(self, __context):
        """Validate that exactly one of totp_token or backup_code is provided."""
        if self.totp_token is None and self.backup_code is None:
            raise ValueError("Either totp_token or backup_code must be provided")
        if self.totp_token is not None and self.backup_code is not None:
            raise ValueError("Only one of totp_token or backup_code should be provided, not both")


class MFAVerifyResponse(BaseModel):
    """Response schema for MFA verification.

    Indicates whether the verification was successful and provides information
    about remaining backup codes if a backup code was used.
    """

    valid: Annotated[bool, Field(description="Whether the verification was successful", examples=[True, False])]
    message: Annotated[str, Field(description="Result message", examples=["Verification successful", "Invalid code"])]
    backup_codes_remaining: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=10,
            description="Number of unused backup codes (only when backup code was used)",
            examples=[9],
        ),
    ]


# =============================================================================
# MFA Disable Schemas
# =============================================================================


class MFADisableRequest(BaseModel):
    """Request schema for disabling MFA.

    Requires password verification for security. Optionally accepts TOTP token
    for additional verification layer.
    """

    password: Annotated[
        str, Field(min_length=8, description="User's current password for verification", examples=["SecureP@ssw0rd123"])
    ]
    totp_token: Annotated[
        str | None,
        Field(
            default=None,
            min_length=6,
            max_length=6,
            pattern=r"^\d{6}$",
            description="Optional 6-digit TOTP code for additional verification",
            examples=["123456"],
        ),
    ]


# =============================================================================
# MFA Status Schemas
# =============================================================================


class MFAStatusResponse(BaseModel):
    """Response schema for MFA status check.

    Provides information about MFA configuration and usage.
    """

    is_enabled: Annotated[
        bool, Field(description="Whether MFA is currently enabled for the user", examples=[True, False])
    ]
    backup_codes_remaining: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=10,
            description="Number of unused backup codes (null if MFA not enabled)",
            examples=[8],
        ),
    ]
    last_used: Annotated[
        str | None,
        Field(
            default=None,
            description="ISO 8601 timestamp of last MFA usage (null if never used)",
            examples=["2026-02-05T10:30:00Z"],
        ),
    ]

    @field_validator("last_used")
    @classmethod
    def validate_iso_timestamp(cls, v: str | None) -> str | None:
        """Validate that last_used is a valid ISO 8601 timestamp."""
        if v is None:
            return None

        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("last_used must be a valid ISO 8601 timestamp")

        return v


# =============================================================================
# MFA Backup Codes Schemas
# =============================================================================


class MFABackupCodesResponse(BaseModel):
    """Response schema for backup codes regeneration.

    Returns new backup codes after regeneration.
    """

    message: Annotated[
        str,
        Field(default="New backup codes generated", description="Success message"),
    ]
    backup_codes: Annotated[
        list[str],
        Field(
            min_length=10,
            max_length=10,
            description="List of new backup codes (format: BC-XXXXX-XXXXX)",
        ),
    ]


# =============================================================================
# MFA Login Completion Schemas
# =============================================================================


class MFACompleteLoginRequest(BaseModel):
    """Request schema for completing MFA login.

    Used to complete the login flow after password authentication when MFA is enabled.
    """

    mfa_token: Annotated[
        str,
        Field(
            min_length=32,
            max_length=64,
            description="MFA pending token from initial login",
        ),
    ]
    totp_token: Annotated[
        str | None,
        Field(
            default=None,
            min_length=6,
            max_length=6,
            pattern=r"^\d{6}$",
            description="6-digit TOTP code from authenticator app",
            examples=["123456"],
        ),
    ]
    backup_code: Annotated[
        str | None,
        Field(
            default=None,
            min_length=11,
            max_length=14,
            description="Backup code (format: BC-XXXXX-XXXXX or XXXXX-XXXXX)",
            examples=["BC-ABCDE-FGHIJ", "ABCDE-FGHIJ"],
        ),
    ]

    @field_validator("backup_code")
    @classmethod
    def normalize_backup_code(cls, v: str | None) -> str | None:
        """Normalize backup code to uppercase and validate format."""
        if v is None:
            return None

        # Convert to uppercase
        normalized = v.upper().strip()

        # Validate format (BC-XXXXX-XXXXX or XXXXX-XXXXX)
        if not _BACKUP_CODE_PATTERN.match(normalized):
            raise ValueError("Invalid backup code format. Expected: BC-XXXXX-XXXXX or XXXXX-XXXXX")

        # Ensure BC- prefix exists
        if not normalized.startswith("BC-"):
            normalized = f"BC-{normalized}"

        return normalized

    @field_validator("totp_token")
    @classmethod
    def validate_totp_token_format(cls, v: str | None) -> str | None:
        """Validate TOTP token format."""
        if v is None:
            return None

        if not _TOTP_TOKEN_PATTERN.match(v):
            raise ValueError("TOTP token must be exactly 6 digits")

        return v

    def model_post_init(self, __context):
        """Validate that exactly one of totp_token or backup_code is provided."""
        if self.totp_token is None and self.backup_code is None:
            raise ValueError("Either totp_token or backup_code must be provided")
        if self.totp_token is not None and self.backup_code is not None:
            raise ValueError("Only one of totp_token or backup_code should be provided, not both")


class MFACompleteLoginResponse(BaseModel):
    """Response schema for successful MFA login completion.

    Returns full access tokens after successful MFA verification.
    """

    access_token: Annotated[
        str,
        Field(description="JWT access token for authenticated requests"),
    ]
    token_type: Annotated[
        str,
        Field(default="bearer", description="Token type"),
    ]
    message: Annotated[
        str,
        Field(default="Login successful", description="Success message"),
    ]
