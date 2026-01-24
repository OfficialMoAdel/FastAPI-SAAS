"""Prices module - API compatibility layer for frontend.

This module provides pricing management endpoints at /api/v1/prices
to maintain compatibility with the old backend. It delegates to the
billing module's PricingService for actual business logic.
"""

from .router import router

__all__ = ["router"]
