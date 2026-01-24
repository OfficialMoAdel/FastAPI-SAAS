"""Products module - API compatibility layer for frontend.

This module provides product management endpoints at /api/v1/products
to maintain compatibility with the old backend. It delegates to the
billing module's ProductService for actual business logic.
"""

from .router import router

__all__ = ["router"]
