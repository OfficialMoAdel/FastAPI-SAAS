"""Invoices module - API compatibility layer for frontend.

This module provides invoice management endpoints at /api/v1/invoices
to maintain compatibility with the old backend. It delegates to the
billing module's BillingRecordService for actual business logic.
"""

from .router import router

__all__ = ["router"]
