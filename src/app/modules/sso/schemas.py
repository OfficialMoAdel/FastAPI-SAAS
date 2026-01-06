from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


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


class UserSSOConnectionCreate(UserSSOConnectionBase):
    user_id: int
    tenant_id: int
    access_token: bytes | None = None
    refresh_token: bytes | None = None


class UserSSOConnectionUpdate(BaseModel):
    provider_email: str | None = None
    access_token: bytes | None = None
    refresh_token: bytes | None = None
    token_expires_at: datetime | None = None


class UserSSOConnectionRead(UserSSOConnectionBase):
    id: int
    user_id: int
    tenant_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SSOProviderRead(BaseModel):
    name: SSOProvider
    enabled: bool
    display_name: str


class SSOProvidersResponse(BaseModel):
    providers: list[SSOProviderRead]
