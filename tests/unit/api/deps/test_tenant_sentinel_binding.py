"""P1-6: tenant-binding guards must test sentinel IDENTITY, not truthiness.

``NO_TENANT.__bool__`` returns False by design, so ``if not current_tenant:``
skipped the binding check on tenant-exempt paths rather than failing it. A
non-superuser credential presented against an exempt path was therefore never
bound to a tenant.

The sentinel itself is fail-closed (tenant_id is the all-zeros UUID), so the
fix belongs in the guard, not the sentinel.

Ordering note: the superuser bypass runs BEFORE the sentinel check. Tenant-
exempt prefixes such as /api/v1/tenants bind NO_TENANT and are gated by
get_current_superuser, so checking the sentinel first would 403 the
cross-tenant operator surface.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"

# denial() renders the DenialReason message. The exception TYPE is ambiguous
# (UnauthorizedError covers several reasons), so the message is the marker.
NO_TENANT_MESSAGE = "tenant context required"
MISMATCH_MESSAGE = "does not belong to the current tenant"
API_KEY_MISMATCH_MESSAGE = "does not match the requested tenant context"


def _tenant_context(tenant_id: str, is_active: bool = True):
    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=UUID(tenant_id),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=is_active,
    )


def _user(tenant_id: str, is_superuser: bool = False):
    return {
        "id": 1,
        "email": "u@example.com",
        "tenant_id": UUID(tenant_id),
        "is_superuser": is_superuser,
        "is_active": True,
    }


def _assert_denied(exc: Exception, marker: str) -> None:
    assert marker in str(exc).lower(), f"expected {marker!r}, got: {exc}"


class TestEnforceUserTenantMatch:
    """_enforce_user_tenant_match must deny the sentinel, not skip it."""

    def test_no_tenant_sentinel_is_denied(self) -> None:
        """The exact regression: a non-superuser must not pass on the sentinel."""
        from src.app.api.deps.auth import _enforce_user_tenant_match
        from src.app.core.tenancy.context import NO_TENANT

        assert not NO_TENANT, "sentinel must stay falsy — that is why this test exists"

        with pytest.raises(Exception) as exc:
            _enforce_user_tenant_match(_user(TENANT_A), NO_TENANT)

        _assert_denied(exc.value, NO_TENANT_MESSAGE)

    def test_none_tenant_is_denied(self) -> None:
        from src.app.api.deps.auth import _enforce_user_tenant_match

        with pytest.raises(Exception) as exc:
            _enforce_user_tenant_match(_user(TENANT_A), None)

        _assert_denied(exc.value, NO_TENANT_MESSAGE)

    def test_superuser_bypasses_before_sentinel_check(self) -> None:
        """A superuser on the sentinel is allowed — operator routes need it.

        /api/v1/tenants is tenant-exempt and superuser-gated, so it binds
        NO_TENANT. Denying there would 403 the cross-tenant operator surface.
        """
        from src.app.api.deps.auth import _enforce_user_tenant_match
        from src.app.core.tenancy.context import NO_TENANT

        _enforce_user_tenant_match(_user(TENANT_A, is_superuser=True), NO_TENANT)
        _enforce_user_tenant_match(_user(TENANT_A, is_superuser=True), None)

        # A superuser is not tenant-scoped, so no mismatch error either.
        _enforce_user_tenant_match(_user(TENANT_A, is_superuser=True), _tenant_context(TENANT_B))

    def test_matching_tenant_passes(self) -> None:
        from src.app.api.deps.auth import _enforce_user_tenant_match

        _enforce_user_tenant_match(_user(TENANT_A), _tenant_context(TENANT_A))

    def test_mismatched_tenant_is_denied(self) -> None:
        from src.app.api.deps.auth import _enforce_user_tenant_match

        with pytest.raises(Exception) as exc:
            _enforce_user_tenant_match(_user(TENANT_A), _tenant_context(TENANT_B))

        _assert_denied(exc.value, MISMATCH_MESSAGE)

    def test_deactivated_tenant_is_not_the_sentinel(self) -> None:
        """A deactivated tenant must still be compared, not treated as exempt.

        Identity is the correct test. A fix keying on ``is_active`` instead
        would deny a real tenant's own users.
        """
        from src.app.api.deps.auth import _enforce_user_tenant_match

        deactivated = _tenant_context(TENANT_A, is_active=False)

        # Its own user is still allowed: bound, not rejected.
        _enforce_user_tenant_match(_user(TENANT_A), deactivated)

        # Another tenant's user is still refused.
        with pytest.raises(Exception) as exc:
            _enforce_user_tenant_match(_user(TENANT_B), deactivated)
        _assert_denied(exc.value, MISMATCH_MESSAGE)


class TestApiKeyTenantGuard:
    """get_user_by_api_key must not skip the comparison on the sentinel."""

    @staticmethod
    def _key_patch(key_info):
        return patch(
            "src.app.modules.api_keys.service.APIKeyService.validate_api_key",
            new_callable=AsyncMock,
            return_value=key_info,
        )

    @pytest.mark.asyncio
    async def test_sentinel_denies_api_key(self) -> None:
        from src.app.api.deps.auth import get_user_by_api_key
        from src.app.core.tenancy.context import NO_TENANT

        key_info = {"user_id": 1, "tenant_id": UUID(TENANT_A), "scopes": [], "key_id": "k"}

        with (
            self._key_patch(key_info),
            patch("src.app.api.deps.auth.get_current_tenant", return_value=NO_TENANT),
        ):
            with pytest.raises(Exception) as exc:
                await get_user_by_api_key("some-key", MagicMock(), MagicMock())

        _assert_denied(exc.value, NO_TENANT_MESSAGE)

    @pytest.mark.asyncio
    async def test_wrong_tenant_denies_api_key(self) -> None:
        from src.app.api.deps.auth import get_user_by_api_key

        key_info = {"user_id": 1, "tenant_id": UUID(TENANT_A), "scopes": [], "key_id": "k"}

        with (
            self._key_patch(key_info),
            patch("src.app.api.deps.auth.get_current_tenant", return_value=_tenant_context(TENANT_B)),
        ):
            with pytest.raises(Exception) as exc:
                await get_user_by_api_key("some-key", MagicMock(), MagicMock())

        _assert_denied(exc.value, API_KEY_MISMATCH_MESSAGE)

    @pytest.mark.asyncio
    async def test_matching_tenant_api_key_passes(self) -> None:
        """Anti-over-fix: a correctly bound key still authenticates."""
        from src.app.api.deps.auth import get_user_by_api_key
        from src.app.modules.users import UserDirectory

        key_info = {"user_id": 1, "tenant_id": UUID(TENANT_A), "scopes": ["read"], "key_id": "k"}
        found = MagicMock()
        found.model_dump.return_value = {
            "id": 1,
            "email": "u@example.com",
            "tenant_id": UUID(TENANT_A),
            "is_active": True,
            "is_deleted": False,
        }

        with (
            self._key_patch(key_info),
            patch("src.app.api.deps.auth.get_current_tenant", return_value=_tenant_context(TENANT_A)),
            patch.object(UserDirectory, "by_id", new_callable=AsyncMock, return_value=found),
        ):
            user = await get_user_by_api_key("some-key", MagicMock(), MagicMock())

        assert user is not None
        assert user.api_key_scopes == ["read"]
