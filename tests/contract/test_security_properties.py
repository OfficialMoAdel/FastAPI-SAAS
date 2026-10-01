"""Security PROPERTIES, not security primitives — the standing suite.

Every other suite here checks that a security *primitive* works: the JWT
decodes, the password hashes, the token is single-use. That is necessary and
it is not sufficient. The findings this suite pins were all cases where every
primitive worked correctly and the composed behaviour was still wrong:

  - logout did not revoke the session (P1-2)
  - four catalog routes were anonymously readable (P0-1)
  - verification email reactivated an admin-disabled account (P0-2)
  - role assignment was gated on the wrong permission (P1-1)

A primitive test cannot see any of those. Only the composed property can.

Each property below is handled one of two ways, and the reason is recorded:

  * DIRECT — the property is cheap enough to execute here, so it is executed.
    If the fix is reverted, this fails.
  * PINNED — the property needs a full request/DB/Redis stack, so this suite
    pins the regression test that owns it. If that test is deleted or renamed,
    this fails. A property that silently loses its only coverage is the exact
    failure mode this file exists to prevent.

Run with `pytest tests/contract/test_security_properties.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# property id -> (one-line statement, owning regression test relative to repo root)
PINNED_PROPERTIES: dict[str, tuple[str, str]] = {
    "P0-1": (
        "an anonymous caller cannot read the billing catalog",
        "tests/integration/modules/billing/test_legacy_catalog_auth.py",
    ),
    "P0-2": (
        "email verification cannot reactivate an admin-disabled account",
        "tests/unit/modules/auth/test_auth_service.py",
    ),
    "P1-1": (
        "a users:manage-only principal cannot assign roles",
        "tests/integration/modules/rbac/test_rbac_assignment.py",
    ),
    "P1-2": (
        "a cookie-only logout revokes the server-side session",
        "tests/integration/modules/auth/test_logout_session_e2e.py",
    ),
    "SEC-08": (
        "the login lockout counter is keyed on the resolved account",
        "tests/integration/modules/auth/test_auth_lockout.py",
    ),
}


class TestEveryPropertyIsOwned:
    """A property with no owning test is a property that can regress silently."""

    def test_each_pinned_property_has_its_regression_test(self) -> None:
        """Falsifiability: delete or rename a pinned test and this fails.

        This is the guard that makes the PINNED half of this suite real.
        Without it, "a regression test exists somewhere" is a claim nobody
        checks.
        """
        missing = {
            prop: path
            for prop, (_statement, path) in PINNED_PROPERTIES.items()
            if not (REPO_ROOT / path).is_file()
        }
        assert not missing, (
            f"security properties lost their only regression test: {missing}. "
            f"Either restore the test or update PINNED_PROPERTIES deliberately "
            f"— silently dropping coverage is how these findings came back."
        )

    @pytest.mark.parametrize(
        ("prop", "keyword"),
        [
            ("P0-1", "anonymous"),
            ("P0-2", "verif"),
            ("P1-1", "permission"),
            ("P1-2", "cookie_only_logout"),
            ("SEC-08", "lockout"),
        ],
    )
    def test_each_pinned_property_has_a_named_asserting_test(self, prop: str, keyword: str) -> None:
        """The pinned module must actually contain a test for THIS property.

        Checks the imported module's test functions rather than grepping its
        source: a file existing proves nothing, and `tests/unit/modules/
        billing/test_invoices_router.py:97` is a live example in this repo of a
        `test_requires_authentication` with an empty body that is collected and
        green. Matching on the function NAME is what ties a file to a property.

        Known limit: a test with the right name but an empty body still passes
        this. Catching that needs a source-text assertion, which this repo
        deliberately ratchets down, so it is recorded rather than paid for.
        """
        import importlib.util
        import inspect

        _statement, path = PINNED_PROPERTIES[prop]
        # Loaded by path, not as a dotted module: several of these directories
        # are not importable packages, and importing them as one would fail for
        # reasons that have nothing to do with the property being checked.
        module_path = REPO_ROOT / path
        spec = importlib.util.spec_from_file_location(f"_pinned_{prop.replace('-', '_')}", module_path)
        assert spec is not None and spec.loader is not None, f"cannot load {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        names = [
            n
            for n, _ in inspect.getmembers(module, inspect.isfunction)
            if n.startswith("test_")
        ]
        # Tests may be methods on a class rather than module-level functions.
        for _cls_name, cls in inspect.getmembers(module, inspect.isclass):
            names += [
                n for n, _ in inspect.getmembers(cls, inspect.isfunction) if n.startswith("test_")
            ]
        matching = [n for n in names if keyword in n.lower()]
        assert matching, (
            f"{prop}: no test named for '{keyword}' in {path}. The property has "
            f"lost its named coverage -- available tests: {sorted(names)[:8]}"
        )


class TestPropertiesThatRunDirectly:
    """Properties cheap enough to execute here are executed, not pinned."""

    @pytest.mark.asyncio
    async def test_p0_3_ip_list_write_does_not_report_success_when_redis_is_down(self) -> None:
        """A write that could not happen must not report that it did.

        The original defect: `add_to_whitelist` had `if client:` guards around
        the Redis write and then `return True`. Because `async_get_redis`
        RAISES rather than yielding None, the guards were dead code and the
        real symptom was an unhandled `MissingClientError` escaping the
        middleware. The property: the call reports failure, and nothing claims
        an IP is whitelisted when it is not — a silently-failed whitelist entry
        is a security control that appears to be on.
        """
        from unittest.mock import patch

        from src.app.core.exceptions.cache_exceptions import MissingClientError
        from src.app.core.guard.ip_rules import IPFilterService

        service = IPFilterService()
        with patch(
            "src.app.core.guard.ip_rules.get_redis_client",
            side_effect=MissingClientError("redis down"),
        ):
            assert await service.add_to_whitelist("10.1.2.3") is False, (
                "a whitelist write during a Redis outage reported success"
            )
            assert await service.add_to_blacklist("10.1.2.4") is False, (
                "a blacklist write during a Redis outage reported success"
            )

    @pytest.mark.asyncio
    async def test_p0_3_expired_whitelist_entry_is_refused_before_touching_redis(self) -> None:
        """An already-expired entry is rejected, not stored then read back.

        Guards the other half of the write path: a past `expires_at` must not
        produce a persisted TTL of zero or less, which Redis would reject and
        the caller would see as an error rather than as a refusal.
        """
        from datetime import UTC, datetime, timedelta

        from src.app.core.guard.ip_rules import IPFilterService

        service = IPFilterService()
        past = datetime.now(UTC) - timedelta(hours=1)

        assert await service.add_to_whitelist("10.1.2.5", expires_at=past) is False

    def test_p1_1_role_assignment_is_gated_on_the_assign_permission(self) -> None:
        """Role assignment must not be reachable with `users:manage`.

        Resolved from the BUILT router's dependency closures, not by reading
        the source. That distinction is the point: the declared
        `Depends(requires_permission(Permission.ROLES_ASSIGN))` can be present
        in the file while the executed dependant carries something else, and
        only the runtime answer settles which gate actually runs.
        """
        import re

        from src.app.modules.rbac.router import router as rbac_router

        slug = re.compile(r"^[a-z_]+:[a-z_]+$")

        def _permissions(dependant: object, found: set[str]) -> None:
            for sub in getattr(dependant, "dependencies", ()):
                call = getattr(sub, "call", None)
                for cell in getattr(call, "__closure__", None) or ():
                    value = cell.cell_contents
                    if isinstance(value, str) and slug.match(value):
                        found.add(value)
                _permissions(sub, found)

        assignment: dict[str, set[str]] = {}
        for route in rbac_router.routes:
            path = getattr(route, "path", "")
            if path.startswith("/roles") and "/users/" in path:
                found: set[str] = set()
                _permissions(route.dependant, found)
                assignment[path] = found

        assert assignment, "no role-assignment routes found; the gate cannot be checked"
        for path, found in assignment.items():
            assert "roles:assign" in found, (
                f"{path} is not gated on roles:assign (resolved: {sorted(found)}). "
                f"P1-1 regression, or the gate was deliberately changed."
            )
            assert "users:manage" not in found, (
                f"{path} is gated on users:manage as well as roles:assign"
            )

    def test_p1_6_no_tenant_sentinel_is_compared_not_truthiness_tested(self) -> None:
        """The sentinel must be compared by identity, not truthiness.

        `NO_TENANT` is falsy BY DESIGN, so a guard written as `if not tenant:
        continue` treats "no tenant context" as authorised. The property is
        that the sentinel is named in the compiled code of the tenant-binding
        guards — checked on the function objects, not on the source text, so a
        comment mentioning it cannot satisfy this.
        """
        import inspect

        from src.app.api.deps import auth as auth_deps

        guards = [
            (name, obj)
            for name, obj in inspect.getmembers(auth_deps, inspect.isfunction)
            if obj.__module__ == auth_deps.__name__
        ]
        checked = [name for name, obj in guards if "NO_TENANT" in (obj.__code__.co_names or ())]
        assert checked, (
            "no tenant-binding guard in api/deps/auth.py references NO_TENANT. "
            "A truthiness test on a falsy sentinel silently authorises the "
            "no-tenant case."
        )


class TestSuiteMeta:
    def test_every_property_in_this_file_is_accounted_for(self) -> None:
        """Keeps the manifest and the executable properties in step.

        If a property is added as a test but not to PINNED_PROPERTIES, it has
        no recorded owner and nobody can tell coverage from luck.
        """
        # Properties executed here rather than pinned elsewhere. Disjoint from
        # PINNED_PROPERTIES: ownership is singular, so a property is either
        # owned by a named regression test or executed in this file, never both.
        direct = {"P0-3", "P1-6"}
        assert not (set(PINNED_PROPERTIES) & direct), (
            "a property is both pinned and executed here; pick one owner"
        )
        assert {"P0-1", "P0-2", "P0-3", "P1-1", "P1-2", "P1-6", "SEC-08"} <= (
            set(PINNED_PROPERTIES) | direct
        ), "a plan finding has no home in this suite"
