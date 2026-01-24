"""Checkouts module - API compatibility layer for frontend.

This module provides subscription management endpoints at /api/v1/checkouts
to maintain compatibility with the old backend. It delegates to the billing
module's CheckoutService and UserSubscriptionService for actual business logic.
"""

from .router import router

__all__ = ["router"]
