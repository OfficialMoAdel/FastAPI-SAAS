"""S-16 — "no tenant" and "exempt" must agree on one cache key.

`NO_TENANT` — the sentinel `TenantMiddleware` binds for every request with no
`X-Tenant-ID` header, for OPTIONS, and for unresolvable slugs on exempt paths
— is falsy WITHOUT being `None`. Two cache-key builders read the context with
`tenant is not None`, so those requests scoped their keys to a phantom
UUID-zero tenant instead of the intended fallback:

    _require_tenant_id          -> user:auth:<uuid-zero>:alice   (should be :global:)
    _build_cache_key            -> tenant:<uuid-zero>:v2:...    (should be public:)

The UUID-zero namespace is unreachable by invalidation.
`build_user_invalidation_delete_keys` derives the tenant from the *user row*
(`None` for a global user) and emits only the `:global:` variant — so a user
revoked on an exempt path kept an auth-info cache entry nothing could delete,
for `AUTH_INFO_CACHE_TTL`. On the decorator side those entries were indexed
into `tenant_index:00000000-...`, a shard no invalidation call can name.

Every other tenancy seam already reads this by truthiness
(`tenancy/query.py:26`, `tenancy/scope.py:83`); these two were the outliers.
"""

from __future__ import annotations

import pytest

from src.app.core.tenancy.context import NO_TENANT, TenantContext
from src.app.core.utils.cache.decorator import _build_cache_key
from src.app.modules.users.cache_keys import _require_tenant_id, build_user_invalidation_delete_keys

ZERO = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _clear_tenant():
    from src.app.core.tenancy.context import set_current_tenant

    set_current_tenant(None)
    yield
    set_current_tenant(None)


def _bind_real_tenant() -> None:
    from src.app.core.tenancy.context import set_current_tenant

    set_current_tenant(
        TenantContext(
            tenant_id="11111111-1111-1111-1111-111111111111",
            tenant_slug="acme",
            tenant_name="Acme",
            is_active=True,
        )
    )


class TestSentinelIsNotATenant:
    def test_no_tenant_sentinel_yields_none(self) -> None:
        from src.app.core.tenancy.context import set_current_tenant

        set_current_tenant(NO_TENANT)
        assert _require_tenant_id() is None, "NO_TENANT must not become a tenant id"

    def test_no_context_yields_none(self) -> None:
        assert _require_tenant_id() is None

    def test_real_tenant_is_used(self) -> None:
        _bind_real_tenant()
        assert _require_tenant_id() == "11111111-1111-1111-1111-111111111111"

    def test_sentinel_and_no_context_agree(self) -> None:
        """The whole point: the two "no tenant" states must be indistinguishable."""
        from src.app.core.tenancy.context import set_current_tenant

        set_current_tenant(NO_TENANT)
        with_sentinel = _require_tenant_id()
        set_current_tenant(None)
        without = _require_tenant_id()
        assert with_sentinel == without


class TestAuthKeyUsesGlobalNamespace:
    def test_invalidation_reaches_what_a_reader_wrote(self) -> None:
        """The delete side must cover the scope the read side writes.

        This is the property that was broken: a reader on an exempt path wrote
        under UUID-zero, while invalidation only ever emitted `:global:`.
        """
        from src.app.core.tenancy.context import set_current_tenant

        set_current_tenant(NO_TENANT)
        read_scope = _require_tenant_id()

        # Invalidation derives tenant from the user row: None => global.
        delete_keys = build_user_invalidation_delete_keys(
            user_id=7, username="alice", email="alice@example.com", tenant_id=None
        )

        # read_scope None => build_tenant_key emits the `:global:` scope,
        # which is exactly what the invalidation side covers.
        assert read_scope is None
        assert any("global:alice" in k for k in delete_keys), delete_keys

    def test_no_uuid_zero_namespace_is_emitted(self) -> None:
        keys = build_user_invalidation_delete_keys(
            user_id=7, username="alice", email="alice@example.com", tenant_id=None
        )
        assert not any(ZERO in k for k in keys), keys


class TestEndpointCacheKey:
    def test_sentinel_routes_to_public_namespace(self) -> None:
        from src.app.core.tenancy.context import set_current_tenant

        set_current_tenant(NO_TENANT)
        key = _build_cache_key("user_post_cache:{username}", 7, {"username": "bob"}, False)

        assert key.startswith("public:"), f"exempt-path entries must be public, got {key}"
        assert ZERO not in key

    def test_real_tenant_still_scopes(self) -> None:
        _bind_real_tenant()
        key = _build_cache_key("user_post_cache:{username}", 7, {"username": "bob"}, False)
        assert key.startswith("tenant:11111111-1111-1111-1111-111111111111:")

    def test_no_context_still_public(self) -> None:
        key = _build_cache_key("user_post_cache:{username}", 7, {"username": "bob"}, False)
        assert key.startswith("public:")
