"""No exempt path may reach a fail-closed tenant accessor.

The exempt paths (health probes, docs, the Stripe receiver, the tenant
bootstrap, the SSO entry, the admin mount) run with the ``NO_TENANT``
sentinel bound. The fail-closed accessors raise ``TenantContextError`` for
that sentinel — it is inactive by design — so an exempt path that called one
would 403 every request to it.

Audit (2026-10): no exempt-path module calls ``require_tenant()`` or
``require_active_tenant()``. The billing webhook handlers resolve the tenant
from the payload through unscoped seams instead of the context. This test
keeps that true.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]

#: The modules that serve an exempt path, and why they are exempt.
EXEMPT_PATH_MODULES = (
    ("src/app/modules/health", "health probes (/health, /api/v1/health)"),
    ("src/app/modules/app_configuration", "public app config (/api/v1/app_configuration)"),
    ("src/app/modules/tenants", "tenant bootstrap (/api/v1/tenants prefix)"),
    (
        "src/app/modules/billing/webhook",
        "the Stripe receiver's handler path (/api/v1/billing/webhook)",
    ),
    ("src/app/modules/auth/routers/sso.py", "SSO entry (/api/v1/account/sso prefix)"),
    ("src/app/api/monitoring.py", "the metrics surface (/api/metrics)"),
    ("src/app/admin", "the operator admin mount"),
)

#: The accessors that raise for a missing or inactive tenant.
FAIL_CLOSED_ACCESSORS = frozenset({"require_tenant", "require_active_tenant"})


def _python_files(relative: str) -> list[Path]:
    path = REPO_ROOT / relative
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def _called_names(source: str) -> set[str]:
    """Every function name CALLED in the source (bare or attribute form)."""
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


@pytest.mark.parametrize(
    ("relative", "reason"),
    EXEMPT_PATH_MODULES,
    ids=[relative for relative, _ in EXEMPT_PATH_MODULES],
)
def test_exempt_path_modules_never_call_a_fail_closed_accessor(relative: str, reason: str) -> None:
    """An exempt path must not reach for a tenant it does not have."""
    files = _python_files(relative)
    assert files, f"no python files found under {relative} — the exemption list is stale"

    offenders: list[str] = []
    for path in files:
        called = _called_names(path.read_text(encoding="utf-8"))
        for accessor in sorted(called & FAIL_CLOSED_ACCESSORS):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {accessor}")

    assert not offenders, (
        f"{relative} serves an exempt path ({reason}) and must not call the "
        f"fail-closed tenant accessors — they raise for the NO_TENANT sentinel: {offenders}"
    )
