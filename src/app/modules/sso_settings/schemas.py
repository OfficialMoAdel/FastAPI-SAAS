from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SSOSettingsBase(BaseModel):
    provider: str
    additional_config: dict | None = None
    is_enabled: bool = True


class SSOSettingsCreate(SSOSettingsBase):
    client_id: str
    client_secret: str


class SSOSettingsCreateInternal(SSOSettingsBase):
    client_id: bytes
    client_secret: bytes


class SSOSettingsUpdate(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsUpdateInternal(BaseModel):
    client_id: bytes | None = None
    client_secret: bytes | None = None
    additional_config: dict | None = None
    is_enabled: bool | None = None


class SSOSettingsRead(SSOSettingsBase):
    id: int
    created_at: datetime
    updated_at: datetime
    # Secrets are intentionally excluded from read schema

    model_config = ConfigDict(from_attributes=True)


class SSOSettingsReadWithSecrets(SSOSettingsRead):
    client_id: str
    client_secret: str
