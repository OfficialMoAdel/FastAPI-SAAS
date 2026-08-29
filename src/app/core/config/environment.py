"""R2-03: settings split from core/config.py — environment domain."""

from enum import Enum

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class EnvironmentOption(str, Enum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class EnvironmentSettings(BaseSettings):
    ENVIRONMENT: EnvironmentOption = EnvironmentOption.LOCAL


class StorageSettings(BaseSettings):
    STORAGE_PROVIDER: str = "s3"  # Options: s3, local (future)
    STORAGE_BUCKET: str = "fastapi-saas-user-content"
    STORAGE_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: SecretStr | None = None
    AWS_ENDPOINT_URL: str | None = None  # For localstack or MinIO


class CORSSettings(BaseSettings):
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]  # SECURITY: Restrict to specific origins
    CORS_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    CORS_HEADERS: list[str] = [
        "Content-Type",
        "Authorization",
        "X-Request-ID",
        "X-Webhook-Signature",
        "X-Webhook-Event",
        "X-Webhook-ID",
        "X-Webhook-Timestamp",
        "X-Tenant-ID",
    ]
