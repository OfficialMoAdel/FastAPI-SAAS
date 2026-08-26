"""
App Configuration Schemas

Pydantic models for frontend application configuration responses.
"""

from pydantic import BaseModel, ConfigDict, Field


class ThemeConfig(BaseModel):
    """Theme configuration for the frontend."""

    primary_color: str = Field(default="#3B82F6", description="Primary brand color")
    secondary_color: str = Field(default="#10B981", description="Secondary brand color")
    dark_mode_enabled: bool = Field(default=True, description="Whether dark mode is available")
    default_theme: str = Field(default="light", description="Default theme (light/dark)")


class FeaturesConfig(BaseModel):
    """Feature flags for the frontend."""

    sso_enabled: bool = Field(default=True, description="SSO authentication enabled")
    passkey_enabled: bool = Field(default=True, description="Passkey authentication enabled")
    magic_link_enabled: bool = Field(default=True, description="Magic link authentication enabled")
    billing_enabled: bool = Field(default=True, description="Billing features enabled")
    ai_enabled: bool = Field(default=True, description="AI features enabled")
    webhooks_enabled: bool = Field(default=True, description="Webhooks enabled")
    storage_enabled: bool = Field(default=True, description="File storage enabled")
    audit_logs_enabled: bool = Field(default=True, description="Audit logs enabled")
    multi_tenancy_enabled: bool = Field(default=True, description="Multi-tenancy enabled")


class APIConfig(BaseModel):
    """API configuration details."""

    version: str = Field(default="1.0.0", description="API version")
    base_url: str = Field(default="/api/v1", description="API base URL")
    websocket_url: str | None = Field(default=None, description="WebSocket URL if enabled")


class AppConfigurationResponse(BaseModel):
    """Frontend application configuration response."""

    app_name: str = Field(default="FastAPI SaaS", description="Application name")
    app_version: str = Field(default="1.0.0", description="Application version")
    environment: str = Field(default="production", description="Environment (development/staging/production)")
    theme: ThemeConfig = Field(default_factory=ThemeConfig, description="Theme configuration")
    features: FeaturesConfig = Field(default_factory=FeaturesConfig, description="Feature flags")
    api: APIConfig = Field(default_factory=APIConfig, description="API configuration")
    maintenance_mode: bool = Field(default=False, description="Whether maintenance mode is active")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "app_name": "FastAPI SaaS",
                "app_version": "1.0.0",
                "environment": "production",
                "theme": {
                    "primary_color": "#3B82F6",
                    "secondary_color": "#10B981",
                    "dark_mode_enabled": True,
                    "default_theme": "light",
                },
                "features": {
                    "sso_enabled": True,
                    "passkey_enabled": True,
                    "magic_link_enabled": True,
                    "billing_enabled": True,
                    "ai_enabled": True,
                    "webhooks_enabled": True,
                    "storage_enabled": True,
                    "audit_logs_enabled": True,
                    "multi_tenancy_enabled": True,
                },
                "api": {
                    "version": "1.0.0",
                    "base_url": "/api/v1",
                    "websocket_url": None,
                },
                "maintenance_mode": False,
            },
        },
    )
