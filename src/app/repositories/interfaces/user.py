"""User repository protocol."""

from __future__ import annotations

from typing import Any, Protocol


class UserRepository(Protocol):
    """Protocol for user data persistence and queries."""

    async def get(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
        is_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch a single user by id, username, or email."""
        ...

    async def get_with_tier(
        self,
        *,
        id: int | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch user eagerly joined with tier for auth and rate-limiting."""
        ...

    async def get_multi(
        self,
        *,
        offset: int = 0,
        limit: int = 10,
        is_deleted: bool = False,
    ) -> dict[str, Any]:
        """Fetch paginated users with total count."""
        ...

    async def create(self, user_data: Any) -> dict[str, Any]:
        """Create a new user record."""
        ...

    async def update(self, user_id: int, user_data: Any) -> dict[str, Any] | None:
        """Update an existing user record."""
        ...

    async def delete(self, user_id: int) -> bool:
        """Delete (or soft-delete) a user record."""
        ...


__all__ = ["UserRepository"]
