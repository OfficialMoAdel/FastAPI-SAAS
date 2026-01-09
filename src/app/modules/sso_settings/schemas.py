
from pydantic import BaseModel

from .._base import BaseCreate, BaseRead, BaseUpdate


class SSOSettingsBase(BaseModel):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True


class SSOSettingsCreate(BaseCreate):
    """Schema for creating SSO settings."""

    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True
    client_id: str
    client_secret: str


class SSOSettingsCreateInternal(BaseCreate):
    """Internal schema with encrypted credentials."""

    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True
    client_id: bytes
    client_secret: bytes


class SSOSettingsUpdate(BaseUpdate):
    """Schema for updating SSO settings."""

    client_id: str | None = None
    client_secret: str | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsUpdateInternal(BaseUpdate):
    """Internal schema with encrypted credentials."""

    client_id: bytes | None = None
    client_secret: bytes | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsRead(BaseRead):
    """Schema for reading SSO settings.

    Inherits from BaseRead: id, created_at, updated_at
    Secrets are intentionally excluded.
    """

    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True


class SSOSettingsReadWithSecrets(SSOSettingsRead):
    """Schema including decrypted secrets."""

    client_id: str
    client_secret: str
