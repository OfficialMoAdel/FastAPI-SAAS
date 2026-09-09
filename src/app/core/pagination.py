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

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query, status
from sqlalchemy import and_, or_
from sqlalchemy.sql import ColumnElement, Select

from src.app.core.config import settings

# L2: Centralized pagination constants
# Default: 20 items per page (balances UX and performance)
# Max: 100 items per page (prevents excessive memory/DB load)
DEFAULT_PER_PAGE: int = settings.app.PAGINATION_DEFAULT_PER_PAGE
MAX_PER_PAGE: int = settings.app.PAGINATION_MAX_PER_PAGE


# R1-02: deep OFFSET scans are O(offset) in PostgreSQL — the DB must read and
# discard every skipped row. The previous 1_000_000 cap allowed page=50000 with
# per_page=20 (OFFSET 999980), a full-table scan per request. Cap at 100_000
# (page 1000 x per_page 100) which still covers any realistic UI pagination
# while bounding worst-case scan cost. True cursor/keyset pagination for
# unbounded traversal is tracked separately (P2/P3).
_MAX_OFFSET = 100_000


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
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Pagination offset {self.offset} exceeds {_MAX_OFFSET}",
            )

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


def encode_keyset_cursor(created_at: datetime, id: int | str) -> str:
    """Encode a ``(created_at, id)`` keyset position into an opaque cursor.

    The cursor is the URL-safe base64 of ``"<iso-created-at>|<id>"``. It is
    opaque to clients (no format contract to maintain) while staying cheap to
    decode server-side. Used with ``decode_keyset_cursor`` and
    ``keyset_pagination_clause`` for constant-cost pagination — see
    ``paginate_keyset``.
    """
    raw = f"{created_at.isoformat()}|{id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_keyset_cursor(cursor: str) -> tuple[datetime, int | str]:
    """Decode an opaque keyset cursor back to ``(created_at, id)``.

    Raises:
        ValueError: If the cursor is not valid base64, is malformed, or
            contains an unparsable timestamp — callers should surface 422.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        created_at_str, _, id_str = raw.rpartition("|")
        created_at = datetime.fromisoformat(created_at_str)
    except (ValueError, UnicodeDecodeError, binascii.Error) as e:
        raise ValueError(f"Malformed keyset cursor: {cursor[:32]!r}") from e
    # Numeric ids (the integer PKs every adopted site uses) round-trip as
    # ints so SQLAlchemy binds them against Integer columns directly.
    row_id: int | str = int(id_str) if id_str.isdigit() else id_str
    return created_at, row_id


def keyset_pagination_clause(
    created_at_col: Any,
    id_col: Any,
    cursor: tuple[datetime, int | str],
) -> ColumnElement[bool]:
    """Build the strict-keyset WHERE clause for ``(created_at DESC, id DESC)`` paging.

    For a descending ``(created_at, id)`` sort, the page after the cursor is
    every row that sorts strictly before it:
    ``created_at < c`` OR (``created_at = c`` AND ``id < i``) — a single
    row-range seek the ``(tenant_id, created_at DESC, id DESC)`` index serves
    in O(limit) instead of OFFSET's O(offset) scan-and-discard.
    """
    created_at, row_id = cursor
    return or_(
        created_at_col < created_at,
        and_(
            created_at_col == created_at,
            id_col < row_id,
        ),
    )


def build_keyset_response(
    items: list[Any],
    limit: int,
    *,
    get_created_at: Callable[[Any], datetime],
    get_id: Callable[[Any], Any],
) -> dict[str, Any]:
    """Build the response envelope for a keyset-paginated page.

    Unlike offset pages, a keyset page carries no ``total`` (counting the
    remaining set is exactly the O(N) cost keyset avoids) and no ``page``
    number. ``next_cursor`` is present iff the page was full — a short page
    is the natural end of the listing.
    """
    has_more = len(items) == limit
    return {
        "items": items,
        "next_cursor": (
            encode_keyset_cursor(get_created_at(items[-1]), get_id(items[-1])) if has_more and items else None
        ),
        "has_next": has_more,
        "limit": limit,
    }


def paginate_keyset(
    stmt: Select,
    created_at_col: Any,
    id_col: Any,
    cursor: str | None,
    limit: int,
) -> tuple[Select, tuple[datetime, int | str] | None]:
    """Apply keyset (cursor) pagination to a SQLAlchemy ``Select`` (P-M2).

    This is the shared helper for constant-cost pagination on high-volume
    tenant tables where OFFSET pages degrade to O(offset) scans (the audit
    trail alone reaches the 100k-row depth cap). It complements the existing
    offset machinery (``PaginationParams`` / ``get_pagination_params`` /
    ``build_paginated_response``) rather than replacing it: keyset pages
    cannot jump to an arbitrary page, so UIs that need page numbers keep
    using offset, while infinite-scroll and export-style iteration move here.

    Contract:
    - Sort order is fixed: ``created_at DESC, id DESC`` (id is the
      deterministic tie-breaker for equal timestamps, mirroring the
      audit_logs ordering at ``modules/audit_logs/service.py``).
    - ``cursor`` is an opaque base64 token from a previous page's
      ``next_cursor`` (see ``encode_keyset_cursor``); ``None`` starts at
      the newest row.
    - ``limit`` is the page size; callers should reuse MAX_PER_PAGE.

    Args:
        stmt: The tenant-scoped ``Select`` to paginate (filters already
            applied — the keyset clause composes onto them).
        created_at_col: The model's ``created_at`` column attribute.
        id_col: The model's integer id column attribute (tie-breaker).
        cursor: Opaque cursor from the previous page, or None for page one.
        limit: Page size (rows per page).

    Returns:
        Tuple of (paginated statement, decoded cursor or None): the caller
        executes the statement and passes the resulting rows through
        ``build_keyset_response`` to produce the response envelope. The
        decoded cursor is returned for observability/logging only —
        ``build_keyset_response`` re-derives the next cursor from the page's
        last row.

    Raises:
        ValueError: If ``cursor`` is malformed (surface as 422 to clients).

    Remaining OFFSET adoption sites (10 at time of writing — each keeps its
    offset endpoint for page-jump UIs and gains a sibling keyset endpoint as
    traffic warrants):
    1. modules/audit_logs/service.py (query_logs / query_logs_with_total —
       first planned adoption; its COUNT(*) OVER() single-query pattern is
       the reference)
    2. modules/posts/service.py get_posts (adopted — get_posts_keyset)
    3. modules/notifications list paths
    4. modules/users list paths
    5. modules/webhook_endpoints list paths
    6. modules/webhook_deliveries list paths
    7. modules/billing list paths (invoices/subscriptions)
    8. modules/files list paths
    9. modules/rate_limits list paths (tier members)
    10. modules/api_keys list paths
    """
    decoded: tuple[datetime, int | str] | None = None
    if cursor is not None:
        decoded = decode_keyset_cursor(cursor)
        stmt = stmt.where(keyset_pagination_clause(created_at_col, id_col, decoded))
    stmt = stmt.order_by(created_at_col.desc(), id_col.desc()).limit(limit)
    return stmt, decoded


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
