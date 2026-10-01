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
from typing import Annotated
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from src.app.api.deps.auth import AuthenticatedUser
from src.app.core.rbac.superuser_tenant import (
    enforce_superuser_tenant_rule,
    request_tenant_id,
)
from tests.helpers.auth import build_test_auth_context

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


class TestUnparseablePrincipalTenantIsRefused:
    """Defect 1: "absent" and "unparseable" are different states, not one.

    ``request_tenant_id`` maps both to ``None`` — the right primitive for a
    REQUEST tenant, where "no tenant to bind" and "not a UUID" mean the same
    thing. For the PRINCIPAL they do not: a principal that names a tenant we
    cannot resolve is not tenant-less, it is unidentifiable, and the rule
    existed to be fail-CLOSED. The code it replaced skipped the check only
    when the tenant was genuinely ABSENT:

        if current_user_tenant_id is not None and str(...) != str(...):
            raise ForbiddenException(...)

    Routing an unparseable principal into that early return is the fail-open:
    a principal carrying ``tenant_id="acme"`` would act as ANY tenant. The
    fallback therefore keeps the pre-refactor STRING comparison, so an equal
    non-UUID tenant (``1 == 1``) still passes — the old line allowed it, and
    ``AuthenticatedUser.tenant_id`` is typed ``UUID | str | int | None``, so a
    slug or a legacy int is representable.
    """

    @pytest.mark.parametrize("unparseable", ["acme-corp", 7, "not-a-uuid", object()])
    def test_present_but_unparseable_cannot_act_as_another_tenant(self, unparseable) -> None:
        """THE closed fail-open: a slug/int principal is not unbound."""
        from src.app.shared.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError):
            enforce_superuser_tenant_rule(
                user_tenant_id=unparseable, request_tenant=TENANT_A, user_id=1, gate="test"
            )

    @pytest.mark.parametrize("matching", [("acme-corp", "acme-corp"), (7, 7)])
    def test_matching_non_uuid_tenant_still_passes(self, matching) -> None:
        """Anti-over-fix: the pre-refactor line allowed equal non-UUID ids.

        ``str(1) != str(1)`` was False, so a superuser whose tenant is a legacy
        int kept working. Normalizing first and refusing would have turned a
        previously-allowed case into a denial.
        """
        user_tenant, request_tenant = matching
        enforce_superuser_tenant_rule(
            user_tenant_id=user_tenant, request_tenant=request_tenant, user_id=1, gate="test"
        )

    def test_matching_non_uuid_tenant_passes_through_a_tenant_context(self) -> None:
        """``get_current_superuser`` hands over a TenantContext, not a UUID.

        Its ``str()`` is a dataclass repr that equals nothing, so the fallback
        unwraps the id it carries — otherwise the two gates would disagree
        (and this one would deny) on a non-UUID tenant.
        """
        enforce_superuser_tenant_rule(
            user_tenant_id=7, request_tenant=_ctx(7), user_id=1, gate="test"
        )

    def test_unparseable_principal_cannot_act_as_a_different_non_uuid_tenant(self) -> None:
        """Neither side a UUID: the string comparison still has to bite."""
        from src.app.shared.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError):
            enforce_superuser_tenant_rule(
                user_tenant_id="acme-corp", request_tenant="other-corp", user_id=1, gate="test"
            )

    def test_refusal_message_is_the_mismatch_message(self) -> None:
        """The client learns it is not bound to this tenant, and nothing else.

        Same message as a plain mismatch so an unparseable tenant is not an
        oracle for how the failure was detected.
        """
        from src.app.shared.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError, match="does not belong to the current tenant"):
            enforce_superuser_tenant_rule(
                user_tenant_id="acme-corp", request_tenant=TENANT_A, user_id=1, gate="test"
            )

    def test_absent_is_still_unconstrained(self) -> None:
        """The fix must not turn 'genuinely absent' into a denial."""
        enforce_superuser_tenant_rule(
            user_tenant_id=None, request_tenant=TENANT_A, user_id=1, gate="test"
        )

    def test_normalizer_itself_is_unchanged(self) -> None:
        """The primitive keeps its contract; only the CALL SITE branches.

        ``request_tenant_id`` is relied on elsewhere to answer "no tenant" for
        an unparseable value, so it must still do exactly that.
        """
        assert request_tenant_id("acme-corp") is None
        assert request_tenant_id(7) is None


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


class TestGatesAgreeOnTheAuthContextTenant:
    """Defect 2: both gates must read the tenant from the SAME principal.

    ``requires_permission`` read the tenant off the resolved ``AuthContext``;
    ``get_current_superuser`` read ``get_current_tenant()`` — the ContextVar.
    That is the second, divergent resolution path the shared rule was created
    to delete, and the two gates disagree whenever the sources differ.

    The divergence is reachable through the SANCTIONED test seam,
    ``app.dependency_overrides[get_auth_context]`` (tests/helpers/auth.py): a
    test can inject ``tenant=A`` while the ContextVar is unset or bound to
    ``NO_TENANT``. Before the fix ``get_current_superuser`` short-circuited on
    ``if not request_tenant`` and admitted the caller, while
    ``requires_permission`` compared against A and refused — the same router,
    two rules, on adjacent mutations.
    """

    @staticmethod
    def _app(ctx, user):
        """A mini-app whose two gates both run production code.

        ``get_auth_context`` is overridden (the sanctioned seam) and
        ``get_current_user`` too, so nothing needs a real token; everything
        below the overrides — the gate bodies and the shared rule — is real.
        """
        from fastapi import Depends, FastAPI

        from src.app.api.deps.auth import get_current_superuser, get_current_user
        from src.app.core.db.database import async_get_db
        from src.app.core.errors import setup_exception_handlers
        from src.app.core.principal import get_auth_context
        from src.app.core.rbac import Permission, requires_permission

        app = FastAPI()
        setup_exception_handlers(app)

        @app.get("/superuser-gate")
        async def _superuser_gate(
            user: Annotated[AuthenticatedUser, Depends(get_current_superuser)],
        ) -> dict:
            return {"user_id": user.id}

        @app.get("/permission-gate", dependencies=[Depends(requires_permission(Permission.ROLES_MANAGE))])
        async def _permission_gate() -> dict:
            return {"ok": True}

        app.dependency_overrides[get_auth_context] = lambda: ctx
        app.dependency_overrides[get_current_user] = lambda: ctx.user
        app.dependency_overrides[async_get_db] = lambda: MagicMock()
        return app

    @staticmethod
    def _status(app, path, context_tenant):
        """GET ``path`` with the ContextVar bound to ``context_tenant``."""
        from fastapi.testclient import TestClient

        from src.app.core.tenancy.lifecycle import bound_tenant

        with bound_tenant(context_tenant):
            return TestClient(app).get(path).status_code

    @pytest.mark.parametrize("url", ["/superuser-gate", "/permission-gate"])
    def test_unset_contextvar_does_not_widen_the_superuser_gate(self, url) -> None:
        """THE defect: the principal's tenant is A, only the ContextVar is empty.

        Pre-fix this returned 200 on the superuser gate and 403 on the
        permission gate — the drift the shared rule exists to prevent.
        """
        ctx = build_test_auth_context(_user(TENANT_A), _ctx(TENANT_B))
        app = self._app(ctx, ctx.user)

        assert self._status(app, url, None) == 403, (
            "a gate admitted a principal acting as another tenant while its "
            "sibling refused"
        )

    @pytest.mark.parametrize("url", ["/superuser-gate", "/permission-gate"])
    def test_no_tenant_sentinel_does_not_widen_the_superuser_gate(self, url) -> None:
        """``NO_TENANT`` is falsy, so it short-circuited the old call site too."""
        from src.app.core.tenancy.context import NO_TENANT

        ctx = build_test_auth_context(_user(TENANT_A), _ctx(TENANT_B))
        app = self._app(ctx, ctx.user)

        assert self._status(app, url, NO_TENANT) == 403

    @pytest.mark.parametrize("url", ["/superuser-gate", "/permission-gate"])
    def test_both_gates_allow_the_principals_own_tenant(self, url) -> None:
        """The regression lock: a superuser in their own tenant still works.

        The ContextVar deliberately names a DIFFERENT tenant — the gate must
        read the principal, not the ContextVar, or this would 403.
        """
        ctx = build_test_auth_context(_user(TENANT_A), _ctx(TENANT_A))
        app = self._app(ctx, ctx.user)

        assert self._status(app, url, _ctx(TENANT_B)) == 200

    def test_tenantless_principal_still_passes_the_superuser_gate(self) -> None:
        """No principal tenant, no request tenant: nothing to compare.

        Only the superuser gate, and the asymmetry is DELIBERATE: on a
        tenant-less surface the superuser gate degrades to the superuser
        check (it guards the cross-tenant operator routes), whereas
        ``requires_permission`` fails closed with TENANT_CONTEXT_MISSING
        because a permission check with no tenant has nothing to key on.
        Both behaviours are pinned elsewhere; this records that the fix did
        not change either.
        """
        ctx = build_test_auth_context(_user(None), None)
        app = self._app(ctx, ctx.user)

        assert self._status(app, "/superuser-gate", None) == 200
        assert self._status(app, "/permission-gate", None) == 403  # fail-closed

    def test_get_current_superuser_does_not_read_the_contextvar(self) -> None:
        """Static guard: the second resolution path must not come back."""
        from src.app.api.deps import auth

        source = inspect.getsource(auth.get_current_superuser)
        body = source.split('"""', 2)[-1]  # drop the docstring; it names both.
        assert "get_current_tenant(" not in body, (
            "get_current_superuser re-resolved the request tenant from the "
            "ContextVar instead of reading the AuthContext"
        )

    def test_get_current_superuser_takes_the_auth_context(self) -> None:
        """The tenant source is declared, not inferred."""
        from src.app.api.deps import auth

        source = inspect.getsource(auth.get_current_superuser)
        assert "Depends(get_auth_context)" in source
        assert "auth_context.tenant" in source


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
            result = await get_current_superuser(
                _user(TENANT_A), build_test_auth_context(_user(TENANT_A), _ctx(TENANT_A))
            )
        assert result.is_superuser is True

    @pytest.mark.asyncio
    async def test_active_superuser_cross_tenant_denied(self) -> None:
        """The gap this bead closes: this used to pass.

        The ContextVar is patched to the principal's OWN tenant: the gate must
        refuse anyway, because it reads the AuthContext, not the ContextVar.
        """
        from src.app.api.deps.auth import get_current_superuser

        with patch("src.app.api.deps.auth.get_current_tenant", return_value=_ctx(TENANT_A)):
            with pytest.raises(Exception, match="(?i)does not belong"):
                await get_current_superuser(
                    _user(TENANT_A), build_test_auth_context(_user(TENANT_A), _ctx(TENANT_B))
                )

    @pytest.mark.asyncio
    async def test_tenantless_superuser_still_passes(self) -> None:
        """The cross-tenant operator surface must keep working."""
        from src.app.api.deps.auth import get_current_superuser

        with patch("src.app.api.deps.auth.get_current_tenant", return_value=None):
            result = await get_current_superuser(
                _user(TENANT_A), build_test_auth_context(_user(TENANT_A), None)
            )
        assert result.is_superuser is True
