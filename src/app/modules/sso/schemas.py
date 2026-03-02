from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from src.app.modules._base import BaseCreate, BaseRead, BaseUpdate


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
    """Schema for creating SSO connection."""

    provider: SSOProvider
    provider_user_id: str
    provider_email: str | None = None
    token_expires_at: datetime | None = None
    user_id: int
    tenant_id: UUID
    access_token: bytes | None = None
    refresh_token: bytes | None = None


class UserSSOConnectionUpdate(BaseUpdate):
    """Schema for updating SSO connection."""

    provider_email: str | None = None
    access_token: bytes | None = None
    refresh_token: bytes | None = None
    token_expires_at: datetime | None = None


class UserSSOConnectionRead(BaseRead):
    """Schema for reading SSO connection.

    Inherits from BaseRead: id, created_at, updated_at
    """

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
