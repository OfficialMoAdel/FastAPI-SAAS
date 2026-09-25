"""Body-level tests for S-7: public user endpoints must not leak privilege fields.

Asserts on the actual HTTP response body (not just route.response_model),
covering both return paths of GET /users/me:
1. The service-fetched profile path.
2. The R5-1 fast path, which returns the raw current_user dict directly —
   response_model filtering is the only thing stripping privileged fields there.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

from src.app.modules.users import router as users_router
from src.app.modules.users.directory import UserDirectory
from src.app.modules.users.service import UserService
from tests.conftest import MockUser
from tests.helpers.auth import clear_test_principal, install_test_principal

PRIVILEGED_FIELDS = {"is_superuser", "tenant_id"}
PUBLIC_FIELDS = {"id", "username", "email", "name", "profile_image_url"}

# Must match the client fixture's fake tenant id so the handler's tenant check passes.
TENANT_ID = MockUser.DEFAULT_TENANT_ID
NOW = datetime.now(UTC)


def _full_user_dict() -> dict:
    """A complete user representation including privileged fields."""
    return {
        "id": 1,
        "name": "Test User",
        "username": "testuser",
        "email": "test@example.com",
        "profile_image_url": "https://example.com/avatar.png",
        "tier_id": 1,
        "is_email_verified": True,
        "is_active": True,
        "is_superuser": True,
        "tenant_id": TENANT_ID,
        "created_at": NOW,
        "updated_at": NOW,
    }


class TestUsersMeBodyFiltering:
    def test_service_path_omits_privileged_fields(self, client) -> None:
        """GET /users/me via service fetch: body has no is_superuser/tenant_id."""
        mock_service = Mock(spec=UserService)
        mock_service.resolve_current_profile = AsyncMock(return_value=_full_user_dict())

        # Principal WITHOUT profile fields -> handler takes the service path
        install_test_principal(client.app, {"id": 1, "username": "testuser"}, 1)
        client.app.dependency_overrides[users_router.get_user_service] = lambda: mock_service

        try:
            response = client.get("/api/v1/users/me", headers={"X-Tenant-ID": "1"})
        finally:
            clear_test_principal(client.app)
            client.app.dependency_overrides.pop(users_router.get_user_service, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked in /users/me body: {PRIVILEGED_FIELDS & body.keys()}"
        )

    def test_fast_path_omits_privileged_fields(self, client) -> None:
        """GET /users/me R5-1 fast path: raw current_user dict is filtered by response_model."""
        # Principal WITH profile fields -> service returns it (R5-1 direct path)
        mock_service = Mock(spec=UserService)
        mock_service.resolve_current_profile = AsyncMock(return_value=_full_user_dict())
        install_test_principal(client.app, _full_user_dict(), 1)
        client.app.dependency_overrides[users_router.get_user_service] = lambda: mock_service

        try:
            response = client.get("/api/v1/users/me", headers={"X-Tenant-ID": "1"})
        finally:
            clear_test_principal(client.app)
            client.app.dependency_overrides.pop(users_router.get_user_service, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked via R5-1 fast path: {PRIVILEGED_FIELDS & body.keys()}"
        )


class TestGetUserByIdBodyFiltering:
    def test_get_user_by_id_omits_privileged_fields(self, client) -> None:
        """GET /users/{user_id}: body has no is_superuser/tenant_id."""
        # The route reads through the directory; the one-line
        # UserService.get_profile forward it used is gone.
        mock_directory = Mock(spec=UserDirectory)
        mock_directory.profile = AsyncMock(return_value=_full_user_dict())

        client.app.dependency_overrides[users_router.get_user_directory] = lambda: mock_directory

        try:
            response = client.get("/api/v1/users/1", headers={"X-Tenant-ID": "1"})
        finally:
            client.app.dependency_overrides.pop(users_router.get_user_directory, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked in /users/{{user_id}} body: {PRIVILEGED_FIELDS & body.keys()}"
        )
