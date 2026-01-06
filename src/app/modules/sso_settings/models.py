"""SSOSettings SQLAlchemy model for OAuth provider configuration.

Host-level SSO provider settings (no tenant_id - intentional exception to Constitution VII
similar to host-level configurations). Stores encrypted OAuth client credentials.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.app.core.db.database import Base


class SSOSettings(Base):
    """SSO provider settings model for OAuth configuration.

    Note: This is a host-level table without tenant_id. This is an intentional
    exception to Constitution VII, as SSO provider settings are shared across
    all tenants (similar to infrastructure configuration).

    Attributes:
        id: Primary key (auto-increment).
        provider: OAuth provider name (google, github, microsoft, apple).
        client_id: Encrypted OAuth client ID.
        client_secret: Encrypted OAuth client secret.
        additional_config: Provider-specific settings as JSONB.
        is_enabled: Enable/disable provider.
        created_at: Settings creation timestamp.
        updated_at: Last update timestamp.
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
        onupdate=datetime.utcnow,
        init=False,
    )
