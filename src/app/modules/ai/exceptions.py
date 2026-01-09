"""AI module specific exceptions.

Provides exception classes for AI-related error scenarios.

T134: Session limit exceeded handling
"""

from __future__ import annotations


class SessionLimitExceededError(Exception):
    """Raised when a tenant has reached the maximum number of concurrent sessions.

    Attributes:
        message: Human-readable error message.
        tenant_id: The tenant that exceeded the limit.
        limit: The maximum allowed sessions.
        current_count: The current number of sessions.
    """

    def __init__(
        self,
        tenant_id: int,
        limit: int = 100,
        current_count: int | None = None,
    ):
        """Initialize SessionLimitExceededError.

        Args:
            tenant_id: ID of the tenant that exceeded the limit.
            limit: Maximum allowed concurrent sessions.
            current_count: Current number of sessions for the tenant.
        """
        self.tenant_id = tenant_id
        self.limit = limit
        self.current_count = current_count
        self.message = f"Session limit exceeded. Maximum {limit} concurrent sessions allowed per tenant."
        super().__init__(self.message)


__all__ = ["SessionLimitExceededError"]
