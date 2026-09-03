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
        # Build theme configuration with schema defaults
        theme = ThemeConfig()

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
            app_name=getattr(settings.app, "APP_NAME", "FastAPI SaaS"),
            app_version=getattr(settings.app, "APP_VERSION", "1.0.0") or "1.0.0",
            environment=(
                settings.env.ENVIRONMENT.value
                if hasattr(settings.env.ENVIRONMENT, "value")
                else str(settings.env.ENVIRONMENT)
            ),
            theme=theme,
            features=features,
            api=api,
            maintenance_mode=False,
        )


# Singleton instance
app_configuration_service = AppConfigurationService()
