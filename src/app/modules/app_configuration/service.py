"""
App Configuration Service

Business logic for generating frontend application configuration.
"""

from src.app.core.config import settings
from src.app.modules.app_configuration.schemas import (
    APIConfig,
    AppConfigurationResponse,
    FeaturesConfig,
    ThemeConfig,
)


class AppConfigurationService:
    """Service for generating frontend application configuration."""

    async def get_configuration(self) -> AppConfigurationResponse:
        """Get frontend application configuration.

        Returns configuration data that the frontend needs to initialize,
        including theme settings, feature flags, and API details.
        """
        # Build theme configuration
        theme = ThemeConfig(
            primary_color=getattr(settings, "THEME_PRIMARY_COLOR", "#3B82F6"),
            secondary_color=getattr(settings, "THEME_SECONDARY_COLOR", "#10B981"),
            dark_mode_enabled=getattr(settings, "THEME_DARK_MODE_ENABLED", True),
            default_theme=getattr(settings, "THEME_DEFAULT", "light"),
        )

        # Build features configuration
        features = FeaturesConfig(
            sso_enabled=True,  # SSO module exists
            passkey_enabled=True,  # Passkey module exists
            magic_link_enabled=True,  # Magic link module exists
            billing_enabled=True,  # Billing module exists
            ai_enabled=True,  # AI module exists
            webhooks_enabled=True,  # Webhooks module exists
            storage_enabled=True,  # Storage module exists
            audit_logs_enabled=True,  # Audit logs module exists
            multi_tenancy_enabled=True,  # Multi-tenancy enabled
        )

        # Build API configuration
        api = APIConfig(
            version="1.0.0",
            base_url="/api/v1",
            websocket_url=None,  # WebSocket not yet implemented
        )

        # Return full configuration
        return AppConfigurationResponse(
            app_name=getattr(settings, "APP_NAME", "FastAPI SaaS"),
            app_version=getattr(settings, "APP_VERSION", "1.0.0"),
            environment=settings.env.ENVIRONMENT,
            theme=theme,
            features=features,
            api=api,
            maintenance_mode=getattr(settings, "MAINTENANCE_MODE", False),
        )


# Singleton instance
app_configuration_service = AppConfigurationService()
