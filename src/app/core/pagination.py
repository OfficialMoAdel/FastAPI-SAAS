"""Pagination utilities and constants for API endpoints.

This module provides standardized pagination defaults and helper functions
to ensure consistent pagination behavior across all API endpoints.

Usage:
    from src.app.core.pagination import PaginationParams, get_pagination_params

    @router.get("/items")
    async def list_items(pagination: PaginationParams = Depends(get_pagination_params)):
        offset = (pagination.page - 1) * pagination.per_page
        ...
"""

from dataclasses import dataclass

from fastapi import Query

from .config import settings

# L2: Centralized pagination constants
# Default: 20 items per page (balances UX and performance)
# Max: 100 items per page (prevents excessive memory/DB load)
DEFAULT_PER_PAGE: int = settings.PAGINATION_DEFAULT_PER_PAGE
MAX_PER_PAGE: int = settings.PAGINATION_MAX_PER_PAGE


@dataclass
class PaginationParams:
    """Standard pagination parameters for API endpoints.

    Attributes:
        page: Current page number (1-indexed)
        per_page: Number of items per page
    """

    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @property
    def offset(self) -> int:
        """Calculate offset for database queries (0-indexed)."""
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """Alias for per_page, commonly used in DB queries."""
        return self.per_page


def get_pagination_params(
    page: int = Query(default=1, ge=1, description="Page number"),
    per_page: int = Query(
        default=DEFAULT_PER_PAGE,
        ge=1,
        le=MAX_PER_PAGE,
        description=f"Items per page (max {MAX_PER_PAGE})",
    ),
) -> PaginationParams:
    """FastAPI dependency for standardized pagination parameters.

    Use this dependency to ensure consistent pagination across all endpoints.

    Example:
        @router.get("/users")
        async def list_users(pagination: PaginationParams = Depends(get_pagination_params)):
            offset = pagination.offset
            limit = pagination.limit
            users = await user_service.list(offset=offset, limit=limit)
            return users

    Args:
        page: Page number (1-indexed, validated >= 1)
        per_page: Items per page (validated 1 <= per_page <= MAX_PER_PAGE)

    Returns:
        PaginationParams instance with calculated offset/limit
    """
    return PaginationParams(page=page, per_page=per_page)
