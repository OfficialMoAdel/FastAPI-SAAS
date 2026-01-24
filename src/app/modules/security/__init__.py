"""
Security Module

Provides convenience endpoints for security-related operations,
particularly passkey management. This module acts as a thin wrapper
around the passkey module to maintain backward compatibility with
the old backend's /api/v1/security/* routes.
"""

from src.app.modules.security.router import router

__all__ = ["router"]
