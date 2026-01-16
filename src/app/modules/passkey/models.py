"""PasskeyCredential SQLAlchemy model for WebAuthn credentials.

Stores WebAuthn credentials with encrypted sensitive fields.
Uses Sprint 0 CredentialEncryption for credential_id and public_key encryption.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID as PyUUID

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.users.models import User


class PasskeyCredential(Base):
    """WebAuthn passkey credential model.

    Attributes:
        id: Primary key (auto-increment).
        user_id: Foreign key to user account.
        credential_id: Encrypted WebAuthn credential ID.
        public_key: Encrypted COSE public key.
        counter: Signature counter for replay prevention.
        device_type: Human-readable device name.
        aaguid: Authenticator AAGUID (optional).
        transports: Supported transports (usb, nfc, ble, internal).
        created_at: Credential creation timestamp.
        last_used_at: Last successful authentication timestamp.
    """

    __tablename__ = "passkey_credentials"

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
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
        String(64),  # SHA256 hex digest is 64 characters
        nullable=True,  # Nullable for migration; will be NOT NULL after backfill
        index=True,  # B-tree index for O(log n) lookup
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
    user: Mapped["User"] = relationship(init=False)
