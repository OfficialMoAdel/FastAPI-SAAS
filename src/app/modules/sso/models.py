"""UserSSOConnection SQLAlchemy model for SSO provider links.

Links users to SSO providers with tenant isolation per Constitution VII.
Stores optional encrypted access/refresh tokens for token refresh scenarios.
"""

import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.db.database import Base

if TYPE_CHECKING:
    from src.app.modules.tenants.models import Tenant
    from src.app.modules.users.models import User


class UserSSOConnection(Base):
    """User SSO connection model linking users to OAuth providers.

    Per Constitution VII, this table includes tenant_id for row-level isolation.

    Attributes:
        id: Primary key (auto-increment).
        user_id: Foreign key to user account.
        tenant_id: Foreign key to tenant for row-level isolation.
        provider: OAuth provider name (google, github, microsoft, apple).
        provider_user_id: User ID from provider (sub claim).
        provider_email: Email from provider (may differ from user email).
        access_token: Encrypted access token (optional).
        refresh_token: Encrypted refresh token (optional).
        token_expires_at: Token expiration timestamp.
        created_at: Connection creation timestamp.
        updated_at: Last token refresh timestamp.
    """

    __tablename__ = "user_sso_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_sso_user_provider"),
        UniqueConstraint("provider", "provider_user_id", name="uq_user_sso_provider_user"),
    )

    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True, init=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid_pkg.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
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
    user: Mapped["User"] = relationship(init=False, lazy="selectin")
    tenant: Mapped["Tenant"] = relationship(init=False, lazy="selectin")
