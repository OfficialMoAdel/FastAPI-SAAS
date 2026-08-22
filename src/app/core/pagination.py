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
from typing import Any

from fastapi import Query

from src.app.core.config import settings

# L2: Centralized pagination constants
# Default: 20 items per page (balances UX and performance)
# Max: 100 items per page (prevents excessive memory/DB load)
DEFAULT_PER_PAGE: int = settings.PAGINATION_DEFAULT_PER_PAGE
MAX_PER_PAGE: int = settings.PAGINATION_MAX_PER_PAGE


# Overflow guard: prevent page/per_page combos that produce >1M offset
_MAX_OFFSET = 1_000_000


@dataclass
class PaginationParams:
    """Standard pagination parameters for API endpoints.

    Attributes:
        page: Current page number (1-indexed)
        per_page: Number of items per page
    """

    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    def __post_init__(self) -> None:
        if self.page < 1:
            self.page = 1
        if self.per_page < 1:
            self.per_page = 1
        elif self.per_page > MAX_PER_PAGE:
            self.per_page = MAX_PER_PAGE
        if self.offset > _MAX_OFFSET:
            raise ValueError(f"Pagination offset {self.offset} exceeds {_MAX_OFFSET}")

    @property
    def offset(self) -> int:
        """Calculate offset for database queries (0-indexed)."""
        return max(0, (self.page - 1) * self.per_page)

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
    params = PaginationParams(page=page, per_page=per_page)
    return params


def build_paginated_response(
    items: list[Any],
    total: int,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Build a standardized paginated response dictionary.

    This utility function eliminates duplicate pagination response construction
    across multiple routers.

    Args:
        items: List of items for the current page
        total: Total number of items across all pages
        page: Current page number (1-indexed)
        per_page: Number of items per page

    Returns:
        Dictionary with items and pagination metadata

    Example:
        @router.get("/items")
        async def list_items(pagination: PaginationParams = Depends(get_pagination_params)):
            result = await service.list_items(limit=pagination.limit, offset=pagination.offset)
            return build_paginated_response(
                items=result["data"],
                total=result["total_count"],
                page=pagination.page,
                per_page=pagination.per_page,
            )
    """
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": page * per_page < total,
        "has_previous": page > 1,
    }
