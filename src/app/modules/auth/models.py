"""Authentication and identity database models.

Consolidated models for:
- Password reset & email verification tokens
- SSO provider settings and user connections
- WebAuthn passkey credentials
- Magic link passwordless authentication tokens
- MFA methods (TOTP and backup codes)
"""

from __future__ import annotations

import secrets
import uuid as uuid_pkg
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


def generate_reset_token() -> str:
    """Generate cryptographically secure reset token."""
    return secrets.token_urlsafe(32)


def generate_verification_token() -> str:
    """Generate cryptographically secure verification token."""
    return secrets.token_urlsafe(32)


# ============================================================================
# Password Reset & Email Verification
# ============================================================================


class PasswordResetToken(Base, kw_only=True):
    """Token for password reset requests."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        default_factory=generate_reset_token,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default_factory=lambda: datetime.now(UTC),
    )

    # Relationship
    user: Mapped[User] = relationship("User", init=False, lazy="noload")

    def __repr__(self) -> str:
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id})>"

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if token is valid (not used and not expired)."""
        return not self.used and not self.is_expired


class EmailVerificationToken(Base, kw_only=True):
    """Token for email verification requests."""

    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        default_factory=generate_verification_token,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default_factory=lambda: datetime.now(UTC) + timedelta(hours=24),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default_factory=lambda: datetime.now(UTC),
    )

    # Relationship
    user: Mapped[User] = relationship("User", init=False, lazy="noload")

    def __repr__(self) -> str:
        return f"<EmailVerificationToken(id={self.id}, user_id={self.user_id})>"

    @property
    def is_expired(self) -> bool:
        """Check if token has expired."""
        return datetime.now(UTC) > self.expires_at


# ============================================================================
# SSO Settings & Connections
# ============================================================================


class SSOSettings(Base, kw_only=True):
    """SSO provider settings model for OAuth configuration.

    Host-level table without tenant_id. Stores encrypted OAuth client credentials.
    """

    __tablename__ = "sso_settings"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )
    client_id: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
    )
    client_secret: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
    )
    additional_config: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )


class UserSSOConnection(Base, kw_only=True):
    """User SSO connection model linking users to OAuth providers."""

    __tablename__ = "user_sso_connections"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_user_sso_provider_user"),
        UniqueConstraint("user_id", "provider", name="uq_user_sso_user_provider"),
    )

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    access_token: Mapped[bytes | None] = mapped_column(
        BYTEA,
        nullable=True,
        default=None,
    )
    refresh_token: Mapped[bytes | None] = mapped_column(
        BYTEA,
        nullable=True,
        default=None,
    )
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        init=False,
    )

    # Relationships
    user: Mapped[User] = relationship(init=False, lazy="noload")
    tenant: Mapped[Tenant] = relationship(init=False, lazy="noload")


# ============================================================================
# Passkey (WebAuthn) Credentials
# ============================================================================


class PasskeyCredential(Base, kw_only=True):
    """WebAuthn passkey credential model."""

    __tablename__ = "passkey_credentials"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
        unique=True,
    )
    public_key: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
    )
    device_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    credential_id_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        default=None,
    )
    counter: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    aaguid: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
    )
    transports: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    # Relationships
    user: Mapped[User] = relationship(init=False, lazy="noload")


# ============================================================================
# Magic Links
# ============================================================================


class MagicLink(Base, kw_only=True):
    """Magic link token model for passwordless authentication."""

    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        default=None,
    )
    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        init=False,
    )

    # Relationships
    user: Mapped[User] = relationship(init=False, lazy="noload")


# ============================================================================
# MFA Methods
# ============================================================================


class MFAMethod(Base, kw_only=True):
    """MFA Method model for TOTP and backup codes."""

    __tablename__ = "mfa_methods"
    __table_args__ = (
        UniqueConstraint("user_id", "type", name="uq_mfa_methods_user_type"),
    )

    id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, insert_default=uuid_pkg.uuid4, init=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String(50), nullable=True, init=False)
    type: Mapped[str] = mapped_column(String(20), default="totp", nullable=False, init=False)
    backup_codes: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, insert_default=list, init=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, init=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, init=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), insert_default=func.now(), init=False
    )

    # Relationships
    user: Mapped[User] = relationship(back_populates="mfa_methods", init=False, lazy="noload")


__all__ = [
    "PasswordResetToken",
    "EmailVerificationToken",
    "SSOSettings",
    "UserSSOConnection",
    "PasskeyCredential",
    "MagicLink",
    "MFAMethod",
    "generate_reset_token",
    "generate_verification_token",
]
