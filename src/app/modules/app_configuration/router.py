"""
App Configuration Router

Provides frontend application configuration endpoints.
"""

from fastapi import APIRouter

from src.app.modules.app_configuration.schemas import AppConfigurationResponse
from src.app.modules.app_configuration.service import app_configuration_service

router = APIRouter(prefix="/app_configuration", tags=["configuration"])


@router.get("", response_model=AppConfigurationResponse)
async def get_app_configuration():
    """Get frontend application configuration.

    Returns configuration data needed by the frontend including:
    - Application name and version
    - Environment (development/staging/production)
    - Theme configuration (colors, dark mode)
    - Feature flags (which features are enabled)
    - API configuration (version, base URL)
    - Maintenance mode status

    This endpoint is public and does not require authentication.
    """
    return await app_configuration_service.get_configuration()
