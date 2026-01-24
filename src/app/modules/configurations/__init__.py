"""Configurations module - API compatibility layer for various service configurations.

This module provides configuration management endpoints to maintain compatibility
with the old backend.
"""

from .stripe_router import router

__all__ = ["router"]
