"""Keep the jwt_service DIP seams wired for every test in this directory.

``jwt_service`` resolves token_version and tenant_id through two module
globals, wired by ``register_users_security_lookups()``. That wiring is a
process-level fact: production calls it once from the application lifespan
(``composition._start_users_security_lookups``).

Tests do not boot an app, so several of them wire the seams by hand — and a
test that registers its own lookup usually calls
``reset_jwt_security_lookups()`` afterwards, which nulls BOTH globals. The
registration is what is lost: the tenant seam then stays ``None`` for the
rest of the session, and every later test that resolves a tenant silently
takes the "unwired" branch and returns ``None``.

The result is order-dependent failure in files that contain no bug at all —
they pass alone and fail after a sibling file that happens to run first.

Re-registering is the correct repair rather than leaving the seams nulled:
the production state is "wired", so a test that resets must not be allowed to
change the state every later test observes. The call is idempotent, so
restoring it after each test costs one dict-free rebind.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_jwt_security_lookups():
    """Rewire the lookup seams after any test that unwires them.

    Restore-only on the way out (not also on the way in): entering with the
    wiring already present is what production does, and a test that
    deliberately leaves the seams unwired to assert the fail-closed branch
    should see them unwired for its own duration.
    """
    yield

    from src.app.modules.users import register_users_security_lookups

    register_users_security_lookups()
