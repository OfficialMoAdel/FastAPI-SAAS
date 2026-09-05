"""OpenAPI schema generation and security scheme configuration.

Extracted per PR-09 (Finding #5) from main.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.params import Security

from src.app.api.deps.auth import (
    get_current_superuser,
    get_current_user,
    get_optional_user,
    get_user_by_api_key,
)
from src.app.core.config import settings


def build_custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate custom OpenAPI schema with security schemes and detected protected paths."""
    if app.openapi_schema:
        return app.openapi_schema

    project_name = settings.app.APP_NAME
    version = settings.app.APP_VERSION or "1.0.0"
    description = settings.app.APP_DESCRIPTION

    openapi_schema = get_openapi(
        title=project_name,
        version=version,
        description=description,
        routes=app.routes,
    )

    components = openapi_schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes.update(
        {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Enter JWT token obtained from /api/v1/auth/login",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": "Enter API Key",
            },
        }
    )

    # Identify (path, method) pairs that explicitly depend on authentication.
    # HARDENED: compare against CANONICAL callables imported from their owning modules
    protected: set[tuple[str, str]] = set()
    auth_callables = {
        id(fn) for fn in (get_current_user, get_current_superuser, get_optional_user, get_user_by_api_key)
    }

    for route in app.routes:
        if not hasattr(route, "dependencies"):
            continue
        for dep in getattr(route, "dependencies", []):
            is_auth_dep = isinstance(dep, Security)
            if not is_auth_dep and hasattr(dep, "dependency") and callable(dep.dependency):
                fn = dep.dependency
                if id(fn) in auth_callables:
                    is_auth_dep = True
                else:
                    qualname = f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', '')}"
                    if qualname.startswith("src.app.core.rbac.dependencies.requires_permission"):
                        is_auth_dep = True

            if is_auth_dep and hasattr(route, "path"):
                for http_method in getattr(route, "methods", set()) or set():
                    protected.add((route.path, http_method.upper()))
                break

    # Apply security only to protected routes (keyed by path AND method, so a
    # public GET on a path with a protected POST is not marked secured).
    for path_str, path_item in openapi_schema["paths"].items():
        if not isinstance(path_item, dict):
            continue
        for method_name, method in path_item.items():
            if not isinstance(method, dict):
                continue
            if method.get("security"):
                continue
            if (path_str, method_name.upper()) in protected:
                method.setdefault("security", []).append({"bearerAuth": []})
                method["security"].append({"ApiKeyAuth": []})

    app.openapi_schema = openapi_schema
    return app.openapi_schema


def setup_custom_openapi(app: FastAPI) -> None:
    """Attach custom OpenAPI schema builder to application."""

    def _openapi() -> dict[str, Any]:
        return build_custom_openapi(app)

    app.openapi = _openapi  # type: ignore[method-assign]


__all__ = ["build_custom_openapi", "setup_custom_openapi"]
