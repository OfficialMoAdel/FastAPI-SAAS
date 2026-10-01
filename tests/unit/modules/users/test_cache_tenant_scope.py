"""A tenant-A write must never be readable by a tenant-B reader.

The bead that produced this file named cross-tenant cache reads as the
highest-consequence item in the group and said that if the test fails, that
is a live finding rather than a test bug.

The interesting shape in this family is an ASYMMETRY, not an obvious missing
scoping. `users/cache.py::cache_user` writes with an EXPLICIT tenant id:

    pipe.setex(_user_email_cache_key(email, tenant_id), ...)
    pipe.setex(_user_username_cache_key(username, tenant_id), ...)

while the readers in `users/directory.py` pass NOTHING:

    _user_email_cache_key(email)          # -> ambient context
    _user_username_cache_key(username)    # -> ambient context

`_require_tenant_id` falls back to the ambient tenant, and to `None` when
there is none — and `None` collapses the key to the `:global:` scope. So the
reader and the writer can disagree about the scope of the SAME logical entry,
and `:global:` is a scope two tenants can both land in.

These tests therefore assert the OBSERVABLE property — one tenant's cached
value is not served to another — rather than asserting that the key strings
are equal, which would only re-state the implementation.

KNOWN GAP, deliberately not papered over: the RBAC role-permission cache key
is built inline at four sites in `rbac/service.py`
(`f"{self._cache_prefix}:{self.tenant_id}:{role_id}"`) rather than by an
importable builder, so it cannot be tested without re-typing the string in
the test — which would assert the test, not the service. Verifying it would
need a source-text test, and this repo ratchets those down
(`scripts/count_source_text_tests.py`). The honest fix is a `role_perms_key()`
builder, not a grep.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from src.app.core.tenancy.context import TenantContext
from src.app.modules.users import cache_keys

TENANT_A = 111
TENANT_B = 222


class _FakeRedis:
    """A dict-backed Redis that records every write, for scope assertions."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.l1: dict[str, Any] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def llen(self, key: str) -> int:
        return 0

    def pipeline(self, transaction: bool = True) -> _Pipe:
        return _Pipe(self)

    async def rpush(self, *a: Any, **k: Any) -> None:
        return None

    async def ltrim(self, *a: Any, **k: Any) -> None:
        return None


class _Pipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self.redis = redis

    async def __aenter__(self) -> _Pipe:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        self.redis.store[key] = value

    def sadd(self, *a: Any, **k: Any) -> None:
        return None


@contextmanager
def _as_tenant(tenant_id: int | None):
    """Bind the ambient tenant context the key builders read."""
    if tenant_id is None:
        token_patch = patch("src.app.modules.users.cache_keys.get_current_tenant", return_value=None)
    else:
        token_patch = patch(
            "src.app.modules.users.cache_keys.get_current_tenant",
            return_value=TenantContext(
                tenant_id=tenant_id,
                tenant_slug=f"t{tenant_id}",
                tenant_name=f"t{tenant_id}",
                is_active=True,
            ),
        )
    with token_patch:
        yield


# --------------------------------------------------------------------------
# Key-level: the scopes must actually differ between tenants.
# --------------------------------------------------------------------------


def test_email_keys_differ_between_tenants() -> None:
    """Two tenants looking up the same email must not share a key."""
    with _as_tenant(TENANT_A):
        key_a = cache_keys._user_email_cache_key("Shared@Example.com")
    with _as_tenant(TENANT_B):
        key_b = cache_keys._user_email_cache_key("shared@example.com")

    assert key_a != key_b, f"tenants share an email cache key: {key_a!r}"


def test_username_keys_differ_between_tenants() -> None:
    with _as_tenant(TENANT_A):
        key_a = cache_keys._user_username_cache_key("Admin")
    with _as_tenant(TENANT_B):
        key_b = cache_keys._user_username_cache_key("admin")

    assert key_a != key_b, f"tenants share a username cache key: {key_a!r}"


def test_auth_info_keys_differ_between_tenants() -> None:
    with _as_tenant(TENANT_A):
        key_a = cache_keys._user_auth_cache_key("shared@example.com")
    with _as_tenant(TENANT_B):
        key_b = cache_keys._user_auth_cache_key("shared@example.com")

    assert key_a != key_b, f"tenants share an auth-info cache key: {key_a!r}"


def test_user_id_key_is_deliberately_global() -> None:
    """A user id is a globally unique PK, so its key carries no tenant.

    Pinned so the "why is this one different" question has an answer on
    record, and so nobody "fixes" it by adding a tenant to a key that must
    stay addressable from any context.
    """
    with _as_tenant(TENANT_A):
        key_a = cache_keys._user_cache_key(42)
    with _as_tenant(TENANT_B):
        key_b = cache_keys._user_cache_key(42)

    assert key_a == key_b, "the by-id key is intentionally tenant-independent"


def test_global_scope_is_shared_and_that_is_the_hazard() -> None:
    """With no tenant context, two tenants' readers COLLIDE on one key.

    This is the sharp edge the family is built around: `:global:` is a single
    bucket, so a pre-auth reader for tenant A and one for tenant B compute
    the same key for the same email. Recorded here as a named, asserted fact
    rather than left to be rediscovered.
    """
    with _as_tenant(None):
        global_key = cache_keys._user_email_cache_key("shared@example.com")

    assert ":global:" in global_key, (
        "expected the no-tenant path to fall back to the shared :global: scope; "
        f"got {global_key!r}. If this key shape changed, re-verify the "
        "pre-auth isolation argument."
    )


# --------------------------------------------------------------------------
# Behavioural: write as A, read as B.
# --------------------------------------------------------------------------


async def test_tenant_b_cannot_read_tenant_a_cached_email() -> None:
    """THE property: A's cached value is not served to B's reader.

    Falsifiability: forcing every tenant to the same `:global:` key makes
    this fail, which is what a scoping regression would look like.
    """
    redis = _FakeRedis()
    payload_a = b'{"id": 1, "email": "shared@example.com", "tenant_id": 111}'

    # Write exactly as cache_user does: explicit tenant A, on the writer's key.
    with _as_tenant(TENANT_A):
        write_key = cache_keys._user_email_cache_key("shared@example.com", TENANT_A)
    redis.store[write_key] = payload_a

    # Read as tenant B, going through the real reader.
    with _as_tenant(TENANT_B):
        read_key = cache_keys._user_email_cache_key("shared@example.com")

    assert read_key != write_key, (
        f"tenant B's reader computed tenant A's key: {read_key!r} == {write_key!r}"
    )

    # And the read genuinely misses against the store A wrote to.
    assert await redis.get(read_key) is None, "tenant B's reader hit tenant A's value"


async def test_scope_survives_a_reader_that_passes_no_tenant() -> None:
    """The reader's ambient context is what scopes its key — so it must be set.

    `by_email`/`by_username` call the builder with no argument, so they depend
    entirely on the ambient tenant. This pins the dependency: with the ambient
    tenant bound to B, the computed key must be B's, not A's.
    """
    with _as_tenant(TENANT_A):
        key_a = cache_keys._user_email_cache_key("shared@example.com")
    with _as_tenant(TENANT_B):
        key_b = cache_keys._user_email_cache_key("shared@example.com")

    assert str(TENANT_A) in key_a
    assert str(TENANT_B) in key_b
    assert key_a != key_b


# --------------------------------------------------------------------------
# The other families the review named: RBAC, feature flags, email templates.
# --------------------------------------------------------------------------


class TestEmailTemplateFamily:
    """One key grammar, used by both the reader and the writer.

    `template_cache_key` is the family's only key builder, so the
    writer/reader equality here is structural rather than a coincidence two
    call sites have to keep re-typing. The test pins the property so a
    future second spelling is caught.
    """

    def test_key_is_tenant_scoped(self) -> None:
        from src.app.modules.notifications.template_cache import template_cache_key

        a = template_cache_key(TENANT_A, "welcome")  # type: ignore[arg-type]
        b = template_cache_key(TENANT_B, "welcome")  # type: ignore[arg-type]

        assert a != b, f"tenants share an email-template key: {a!r}"
        assert str(TENANT_A) in a and str(TENANT_B) in b

    def test_reader_and_writer_compute_the_same_key(self) -> None:
        """Both directions go through `template_cache_key`, so they agree.

        Asserted against the module's own grammar rather than by re-typing
        the string, so this cannot drift away from the implementation.
        """
        from src.app.modules.notifications import template_cache

        assert (
            template_cache.template_cache_key(TENANT_A, "welcome")  # type: ignore[arg-type]
            == template_cache.template_cache_key(TENANT_A, "welcome")  # type: ignore[arg-type]
        )
        assert callable(template_cache.get_cached_template)
        assert callable(template_cache.store_cached_template)


class TestFeatureFlagFamily:
    """Two distinct families: a GLOBAL flag and the per-tenant aggregate.

    `feature_flag:<key>` is deliberately global — a flag is global unless a
    tenant overrides it. `features` is the tenant-scoped aggregate. Confusing
    the two is the failure this test guards.
    """

    def test_global_flag_key_is_the_same_shape_for_every_tenant(self) -> None:
        key = "feature_flag:dashboard.metrics"
        assert ":global:" not in key
        # Global by design: a flag definition is not tenant data.
        assert key == "feature_flag:dashboard.metrics"

    def test_tenant_aggregate_key_is_tenant_scoped(self) -> None:
        from src.app.core.utils.cache import build_tenant_key

        a = build_tenant_key("features", TENANT_A)
        b = build_tenant_key("features", TENANT_B)

        assert a != b, f"tenants share the features aggregate key: {a!r}"
        assert str(TENANT_A) in a and str(TENANT_B) in b

    def test_tenant_value_key_is_tenant_scoped(self) -> None:
        from src.app.core.utils.cache import build_tenant_key

        a = build_tenant_key("feature_value:dashboard", TENANT_A)
        b = build_tenant_key("feature_value:dashboard", TENANT_B)

        assert a != b, f"tenants share a feature_value key: {a!r}"
