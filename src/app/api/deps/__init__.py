"""API Dependencies.

This package contains broken down dependencies for better organization.
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
    resolve_principal,
)
from src.app.api.deps.request_context import (
    RequestContext,
    RequestContextDep,
    get_request_context,
    owns_resource,
    require_resource_ownership,
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
    "resolve_principal",
    "get_current_superuser",
    "tenant_dependency",
    "RequestContext",
    "RequestContextDep",
    "get_request_context",
    "owns_resource",
    "require_resource_ownership",
]
