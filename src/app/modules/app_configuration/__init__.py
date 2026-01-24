"""
App Configuration Module

Provides frontend application configuration endpoints.
Returns configuration data needed by the frontend to initialize properly.
"""

from src.app.modules.app_configuration.router import router

__all__ = ["router"]
