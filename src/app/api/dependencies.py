"""API Dependencies Facade.

This module re-exports dependencies from the `deps` package for backward
compatibility. New modules should prefer importing directly from `src.app.api.deps.*`.
"""

from src.app.api.deps.auth import (
    AuthContext,
    AuthenticatedUser,
    _create_auth_context,
    _handle_auth_mocks,
    _is_token_blacklisted_with_fallback,
    _verify_optional_bearer_token,
    get_auth_context,
    get_current_superuser,
    get_current_user,
    get_optional_auth_context,
    get_optional_user,
    get_user_by_api_key,
    requires_api_key_scope,
)
from src.app.api.deps.core import rate_limiter_dependency
from src.app.api.deps.tenant import get_current_tenant

__all__ = [
    "AuthenticatedUser",
    "AuthContext",
    "_create_auth_context",
    "_is_token_blacklisted_with_fallback",
    "get_auth_context",
    "get_current_user",
    "get_user_by_api_key",
    "_handle_auth_mocks",
    "_verify_optional_bearer_token",
    "get_optional_auth_context",
    "get_optional_user",
    "requires_api_key_scope",
    "get_current_superuser",
    "get_current_tenant",
    "rate_limiter_dependency",
]
