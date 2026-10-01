"""P1: both superuser gates apply the SAME tenant rule.

The project documents one rule — a superuser may bypass permission checks
within their own tenant, but never act AS another tenant — and had two gates
implementing it differently. ``requires_permission`` pinned the tenant before
the bypass; ``get_current_superuser`` never touched the tenant at all. The
same router therefore enforced two different rules on adjacent mutations.

STEP ZERO (recorded on the bead): this is NOT a cross-tenant write. The write
is filtered by ``TenantAwareCRUD`` (``_resolve_write_tenant`` +
``inject_tenant_filter``), which is fail-closed, so no cross-tenant mutation
succeeds. The residual is the drift: the gate and the service disagree about
where the check lives, and one gate had no check.

These tests pin the shared rule, the equivalence of the two gates, and the
tenant-less surfaces that must keep working.
"""

import ast
import inspect
import re
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.app.core.rbac.superuser_tenant import (
    enforce_superuser_tenant_rule,
    request_tenant_id,
)

TENANT_A = uuid4()
TENANT_B = uuid4()


def _user(tenant, is_superuser=True, is_active=True):
    class _U:
        pass

    u = _U()
    u.tenant_id = tenant
    u.is_superuser = is_superuser
    u.is_active = is_active
    u.id = 1
    return u


def _ctx(tenant_id):
    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


class TestTheRuleItself:
    """The shared helper: one tenant, one behaviour."""

    def test_same_tenant_is_allowed(self) -> None:
        """The regression lock: a superuser still works in their own tenant."""
        enforce_superuser_tenant_rule(
            user_tenant_id=TENANT_A, request_tenant=TENANT_A, user_id=1, gate="test"
        )

    def test_cross_tenant_superuser_is_denied(self) -> None:
        """THE assertion — a superuser gets no pass on another tenant."""
        from src.app.shared.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError):
            enforce_superuser_tenant_rule(
                user_tenant_id=TENANT_A, request_tenant=TENANT_B, user_id=1, gate="test"
            )

    def test_non_superuser_cross_tenant_is_denied_the_same(self) -> None:
        """The rule keys on tenant, not on privilege."""
        from src.app.shared.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError):
            enforce_superuser_tenant_rule(
                user_tenant_id=TENANT_A, request_tenant=TENANT_B, user_id=1, gate="test"
            )

    def test_tenantless_request_is_not_constrained(self) -> None:
        """A tenant-less surface has nothing to compare — exempt paths."""
        enforce_superuser_tenant_rule(
            user_tenant_id=TENANT_A, request_tenant=None, user_id=1, gate="test"
        )

    def test_principal_without_tenant_is_not_constrained(self) -> None:
        """A principal with no tenant cannot be bound; the gate's checks decide."""
        enforce_superuser_tenant_rule(
            user_tenant_id=None, request_tenant=TENANT_A, user_id=1, gate="test"
        )

    def test_string_and_uuid_compare_equal(self) -> None:
        """A transport-format tenant must compare like the domain type."""
        enforce_superuser_tenant_rule(
            user_tenant_id=str(TENANT_A), request_tenant=TENANT_A, user_id=1, gate="test"
        )


class TestRequestTenantNormalization:
    """The normalizer must unwrap every shape a gate can hand it.

    ``get_current_tenant()`` returns a TenantContext, NOT a UUID. Stringifying
    the dataclass yields its repr, which never parses — so an unwrapping
    normalizer is the difference between "denied" and "correct".
    """

    def test_uuid_passes_through(self) -> None:
        assert request_tenant_id(TENANT_A) == TENANT_A

    def test_string_is_parsed(self) -> None:
        assert request_tenant_id(str(TENANT_A)) == TENANT_A

    def test_tenant_context_is_unwrapped(self) -> None:
        assert request_tenant_id(_ctx(TENANT_A)) == TENANT_A

    @pytest.mark.parametrize("bad", [None, "", "garbage", 123, object()])
    def test_unparseable_is_none(self, bad) -> None:
        assert request_tenant_id(bad) is None


class TestGatesShareTheHelper:
    """The point of the refactor: one implementation, both gates.

    A duplicated comparison is a promise someone honours once and forgets the
    other. These assertions fail if a gate ever stops calling the helper.
    """

    def test_requires_permission_calls_the_shared_rule(self) -> None:
        from src.app.core.rbac import dependencies

        source = inspect.getsource(dependencies.requires_permission)
        assert "enforce_superuser_tenant_rule(" in source, (
            "requires_permission no longer uses the shared tenant rule"
        )

    def test_get_current_superuser_calls_the_shared_rule(self) -> None:
        from src.app.api.deps import auth

        source = inspect.getsource(auth.get_current_superuser)
        assert "enforce_superuser_tenant_rule(" in source, (
            "get_current_superuser does not apply the shared tenant rule"
        )

    def test_neither_gate_has_its_own_comparison(self) -> None:
        """No gate may re-inline the comparison and drift again."""
        from src.app.api.deps import auth
        from src.app.core.rbac import dependencies

        # Scoped to the GATE function: the module also contains the helper's
        # own comparison via the import, and unrelated routes.
        inline = re.compile(r"str\([^)]*tenant[^)]*\)\s*!=\s*str\([^)]*tenant", re.I)
        for source, name in (
            (inspect.getsource(dependencies.requires_permission), "requires_permission"),
            (inspect.getsource(auth.get_current_superuser), "get_current_superuser"),
        ):
            assert not inline.search(source), (
                f"{name} re-inlines a tenant comparison instead of the shared rule"
            )

    def test_helper_does_not_import_a_gate(self) -> None:
        """No cycle: the rule is a leaf both gates may import."""
        from src.app.core.rbac import superuser_tenant

        # Check the IMPORT statements, not the prose: the docstring
        # legitimately names both gates.
        tree = ast.parse(inspect.getsource(superuser_tenant))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not any("dependencies" in m or "api.deps" in m for m in imported), (
            f"the rule module imports a gate it is meant to serve: {imported}"
        )


class TestSuperuserGateBehaviour:
    """The gate's own checks are unchanged, plus the new assertion."""

    @pytest.mark.asyncio
    async def test_inactive_superuser_denied(self) -> None:
        from src.app.api.deps.auth import get_current_superuser

        with pytest.raises(Exception, match="(?i)no longer active"):
            await get_current_superuser(_user(TENANT_A, is_active=False))

    @pytest.mark.asyncio
    async def test_non_superuser_denied(self) -> None:
        from src.app.api.deps.auth import get_current_superuser

        with pytest.raises(Exception):
            await get_current_superuser(_user(TENANT_A, is_superuser=False))

    @pytest.mark.asyncio
    async def test_active_superuser_in_own_tenant_passes(self) -> None:
        """REGRESSION LOCK: the over-fix that locks superusers out of their tenant."""
        from src.app.api.deps.auth import get_current_superuser

        with patch("src.app.api.deps.auth.get_current_tenant", return_value=_ctx(TENANT_A)):
            result = await get_current_superuser(_user(TENANT_A))
        assert result.is_superuser is True

    @pytest.mark.asyncio
    async def test_active_superuser_cross_tenant_denied(self) -> None:
        """The gap this bead closes: this used to pass."""
        from src.app.api.deps.auth import get_current_superuser

        with patch("src.app.api.deps.auth.get_current_tenant", return_value=_ctx(TENANT_B)):
            with pytest.raises(Exception, match="(?i)does not belong"):
                await get_current_superuser(_user(TENANT_A))

    @pytest.mark.asyncio
    async def test_tenantless_superuser_still_passes(self) -> None:
        """The cross-tenant operator surface must keep working."""
        from src.app.api.deps.auth import get_current_superuser

        with patch("src.app.api.deps.auth.get_current_tenant", return_value=None):
            result = await get_current_superuser(_user(TENANT_A))
        assert result.is_superuser is True
