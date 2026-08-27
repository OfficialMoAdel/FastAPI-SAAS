"""API Dependencies Facade.

This module re-exports dependencies from the `deps` package for backward
compatibility. New modules should prefer importing directly from `src.app.api.deps.*`.
"""

from src.app.api.deps.auth import (
    AuthContext,
    AuthenticatedUser,
    get_auth_context,
    get_current_superuser,
    get_current_user,
    get_optional_auth_context,
    get_optional_user,
    get_user_by_api_key,
    requires_api_key_scope,
)
from src.app.api.deps.core import rate_limiter_dependency
from src.app.api.deps.request_context import (
    RequestContext,
    RequestContextDep,
    get_request_context,
)
from src.app.api.deps.tenant import tenant_dependency

__all__ = [
    "AuthenticatedUser",
    "AuthContext",
    "get_auth_context",
    "get_current_user",
    "get_user_by_api_key",
    "get_optional_auth_context",
    "get_optional_user",
    "requires_api_key_scope",
    "get_current_superuser",
    "tenant_dependency",
    "rate_limiter_dependency",
    "RequestContext",
    "RequestContextDep",
    "get_request_context",
]
