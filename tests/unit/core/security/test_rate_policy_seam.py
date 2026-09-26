"""The tier-policy fetcher seam: core reads policy rows the app supplies.

When the tier policy cache misses, core falls back to the database for the
tier's path-policy rows. That read reached the rate-limits feature module, which
the H-5 contract forbids from core, so the *fetch* is registered from outside
and core keeps deciding which row applies to a path.

These pin the failure modes, and one detail worth pinning explicitly: the
fetcher replaces a per-call ``FastCRUD(RateLimit)`` construction, so the
seam is also what stops a data access object being rebuilt on every cache miss.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.app.core.security import policies as policy_seam


@pytest.fixture
def unregistered():
    """Unwire the seam, then restore whatever was there."""
    saved = policy_seam._rate_policy_fetcher
    policy_seam.reset_rate_policy_fetcher()
    yield
    policy_seam._rate_policy_fetcher = saved


def test_require_raises_when_unwired(unregistered):
    with pytest.raises(RuntimeError, match="rate policy fetcher is not registered"):
        policy_seam.require_rate_policy_fetcher()


def test_boot_gate_raises_when_unwired(unregistered):
    """The gate exists so the failure lands at startup, not on first cache miss."""
    with pytest.raises(RuntimeError, match="rate policy fetcher is not registered"):
        policy_seam.ensure_rate_policy_fetcher_registered()


def test_application_seam_registers_a_fetcher():
    """Importing the rate-limits package supplies the fetch."""
    import src.app.modules.rate_limits  # noqa: F401 — the import is the registration

    fetch = policy_seam.require_rate_policy_fetcher()
    assert callable(fetch)
    policy_seam.ensure_rate_policy_fetcher_registered()


async def test_the_registered_fetcher_reads_the_tier_rows():
    """It returns a list of rows for the tier, given a session and a tier id."""
    import src.app.modules.rate_limits  # noqa: F401
    from src.app.modules.rate_limits.models import RateLimit

    fetch = policy_seam.require_rate_policy_fetcher()
    captured: dict = {}

    class _Row:
        path = "/api/v1/posts"
        limit = 7
        period = 60

    class _Crud:
        def __init__(self, model):
            captured["model"] = model

        async def get_multi(self, *, db, tier_id, return_total_count):
            captured["tier_id"] = tier_id
            return {"data": [_Row()]}

    import src.app.modules.rate_limits as pkg

    original = pkg.FastCRUD
    pkg.FastCRUD = _Crud
    try:
        rows = await fetch(object(), 42)
    finally:
        pkg.FastCRUD = original

    assert captured["model"] is RateLimit
    assert captured["tier_id"] == 42
    assert [r.path for r in rows] == ["/api/v1/posts"]


def test_reset_is_blocked_in_production(monkeypatch):
    from src.app.core.config import EnvironmentOption

    monkeypatch.setattr(policy_seam.settings.env, "ENVIRONMENT", EnvironmentOption.PRODUCTION)
    with pytest.raises(RuntimeError, match="test-only and blocked in production"):
        policy_seam.reset_rate_policy_fetcher()


def test_core_security_does_not_import_the_feature_model():
    """The edge this seam exists to remove must not creep back."""
    for path in Path("src/app/core/security").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.app.modules.rate_limits"), (
                    f"{path} imports {node.module}; the rows come from a "
                    "registered fetcher instead"
                )
