"""Test-only principal injector — the single seam for faking auth in tests.

Production code (``src/app/api/deps/auth.py`` + ``src/app/core/rbac/dependencies.py``)
knows nothing about tests: ``requires_permission`` consumes a real ``AuthContext``
and always enforces tenant isolation. HTTP tests inject principals by overriding
``get_auth_context`` / ``get_optional_auth_context`` with a real ``AuthContext``;
unit tests pass that ``AuthContext`` directly to the permission checker.

This module is the ONLY place allowed to synthesize principals without a token.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

TEST_EMAIL_FALLBACK = "mocked@example.com"
TEST_USERNAME_FALLBACK = "mockeduser"


def _safe_attr(obj: Any, *names: str, default: Any = None) -> Any:
    """First non-Mock attribute from *names*, else *default*."""
    from unittest.mock import Mock

    for name in names:
        try:
            value = getattr(obj, name)
        except AttributeError:
            continue
        if isinstance(value, Mock):
            continue
        if value is not None:
            return value
    return default


def coerce_user(user: Any) -> Any:
    """Normalize dict / MockUser / model-ish objects to ``AuthenticatedUser``."""
    from src.app.api.deps import AuthenticatedUser

    if isinstance(user, AuthenticatedUser):
        return user
    if isinstance(user, dict):
        data = dict(user)
        data.setdefault("email", TEST_EMAIL_FALLBACK)
        data.setdefault("username", TEST_USERNAME_FALLBACK)
        return AuthenticatedUser(**data)
    # MockUser / ORM-ish objects: pull known attributes, skipping Mock junk.
    data = {}
    for field in (
        "id",
        "uuid",
        "email",
        "username",
        "is_superuser",
        "is_active",
        "is_deleted",
        "tier_id",
        "tenant_id",
        "profile_image_url",
        "token_version",
        "tier",
        "api_key_id",
        "api_key_scopes",
    ):
        value = _safe_attr(user, field)
        if value is not None:
            data[field] = value
    # `getattr` on a Mock auto-creates attributes — drop non-field junk.
    data.pop("roles", None)
    data.pop("permissions", None)
    data.setdefault("email", _safe_attr(user, "email", default=TEST_EMAIL_FALLBACK))
    data.setdefault("username", _safe_attr(user, "username", default=TEST_USERNAME_FALLBACK))
    if not isinstance(data.get("email"), str):
        data["email"] = TEST_EMAIL_FALLBACK
    if not isinstance(data.get("username"), str):
        data["username"] = TEST_USERNAME_FALLBACK
    return AuthenticatedUser(**data)


def coerce_tenant(tenant: Any) -> Any | None:
    """Normalize TenantContext / Mock / dict / UUID / None to ``TenantContext``."""
    if tenant is None:
        return None
    from src.app.core.tenancy.context import TenantContext

    if isinstance(tenant, TenantContext):
        return tenant
    if isinstance(tenant, dict):
        tenant_id = tenant.get("id", tenant.get("tenant_id"))
        return TenantContext(
            tenant_id=tenant_id,
            tenant_slug=tenant.get("slug", tenant.get("tenant_slug", "test-tenant")),
            tenant_name=tenant.get("name", tenant.get("tenant_name", "Test Tenant")),
            is_active=tenant.get("is_active", True),
        )
    if isinstance(tenant, UUID):
        return TenantContext(
            tenant_id=tenant,
            tenant_slug="test-tenant",
            tenant_name="Test Tenant",
            is_active=True,
        )
    if isinstance(tenant, int | str):
        tenant_id = tenant
        try:
            tenant_id = UUID(str(tenant))
        except ValueError:
            pass
        return TenantContext(
            tenant_id=tenant_id,
            tenant_slug="test-tenant",
            tenant_name="Test Tenant",
            is_active=True,
        )
    return TenantContext(
        tenant_id=_safe_attr(tenant, "id", "tenant_id"),
        tenant_slug=_safe_attr(tenant, "slug", "tenant_slug", default="test-tenant"),
        tenant_name=_safe_attr(tenant, "name", "tenant_name", default="Test Tenant"),
        is_active=_safe_attr(tenant, "is_active", default=True),
    )


def build_test_auth_context(user: Any, tenant: Any = None) -> Any:
    """Build a real ``AuthContext`` from test doubles via the production model."""
    from src.app.api.deps import AuthContext

    if tenant is None:
        # Fall back to the principal's own tenant so requires_permission
        # (which reads only auth_context.tenant) doesn't 400.
        tenant = _safe_attr(user, "tenant_id", "tenantId")
    if tenant is None:
        from src.app.core.tenancy.context import get_current_tenant

        tenant = get_current_tenant()
    return AuthContext(user=coerce_user(user), token_data=None, tenant=coerce_tenant(tenant))


@contextmanager
def override_test_principal(user: Any, tenant: Any = None, app: Any = None) -> Iterator[Any]:
    """Override ``get_auth_context`` + ``get_optional_auth_context`` with a test principal.

    Usage (HTTP tests — replaces patching the removed private token helper)::

        with override_test_principal(mock_user, mock_tenant):
            response = await client.get("/api/v1/roles", headers={...})

    Pass ``user=None`` for anonymous flows (401 path): the ``get_auth_context``
    override from any enclosing principal fixture (e.g. ``full_mock_context``)
    is removed for the block so production raises; nothing is re-added.
    The permission dependency then runs the production path: real ``AuthContext``,
    real tenant-isolation enforcement, real bitmask lookup.
    """
    if app is None:
        from src.app.main import app as global_app

        app = global_app
    from src.app.api.deps import get_auth_context, get_optional_auth_context

    if user is None:
        had_required = get_auth_context in app.dependency_overrides
        saved_required = app.dependency_overrides.pop(get_auth_context, None)
        had_optional = get_optional_auth_context in app.dependency_overrides
        saved_optional = app.dependency_overrides.pop(get_optional_auth_context, None)
        try:
            yield None
        finally:
            if had_required:
                app.dependency_overrides[get_auth_context] = saved_required
            if had_optional:
                app.dependency_overrides[get_optional_auth_context] = saved_optional
        return
    ctx = build_test_auth_context(user, tenant)
    app.dependency_overrides[get_auth_context] = lambda: ctx
    app.dependency_overrides[get_optional_auth_context] = lambda: ctx
    try:
        yield ctx
    finally:
        app.dependency_overrides.pop(get_auth_context, None)
        app.dependency_overrides.pop(get_optional_auth_context, None)


def install_test_principal(app: Any, user: Any, tenant: Any = None) -> Any:
    """Fixture-friendly variant of :func:`override_test_principal` (no context manager).

    Pair with ``clear_test_principal(app)`` in fixture teardown.
    """
    from src.app.api.deps import get_auth_context, get_optional_auth_context

    ctx = build_test_auth_context(user, tenant)
    app.dependency_overrides[get_auth_context] = lambda: ctx
    app.dependency_overrides[get_optional_auth_context] = lambda: ctx
    return ctx


def clear_test_principal(app: Any) -> None:
    """Remove principal overrides installed by :func:`install_test_principal`."""
    from src.app.api.deps import get_auth_context, get_optional_auth_context

    app.dependency_overrides.pop(get_auth_context, None)
    app.dependency_overrides.pop(get_optional_auth_context, None)
