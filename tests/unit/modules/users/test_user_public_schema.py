"""Body-level tests for S-7: public user endpoints must not leak privilege fields.

Asserts on the actual HTTP response body (not just route.response_model),
covering both return paths of GET /users/me:
1. The service-fetched profile path.
2. The R5-1 fast path, which returns the raw current_user dict directly —
   response_model filtering is the only thing stripping privileged fields there.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

from src.app.api.deps import get_current_user
from src.app.modules.users import router as users_router
from src.app.modules.users.service import UserService
from tests.conftest import MockUser

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

        # current_user WITHOUT profile fields -> handler takes the service path
        client.app.dependency_overrides[get_current_user] = lambda: {
            "id": 1,
            "username": "testuser",
        }
        client.app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        try:
            response = client.get("/api/v1/users/me", headers={"X-Tenant-ID": "1"})
        finally:
            client.app.dependency_overrides.pop(get_current_user, None)
            client.app.dependency_overrides.pop(users_router._get_user_service, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked in /users/me body: {PRIVILEGED_FIELDS & body.keys()}"
        )

    def test_fast_path_omits_privileged_fields(self, client) -> None:
        """GET /users/me R5-1 fast path: raw current_user dict is filtered by response_model."""
        # current_user WITH profile fields -> handler returns it directly (R5-1)
        client.app.dependency_overrides[get_current_user] = lambda: _full_user_dict()

        try:
            response = client.get("/api/v1/users/me", headers={"X-Tenant-ID": "1"})
        finally:
            client.app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked via R5-1 fast path: {PRIVILEGED_FIELDS & body.keys()}"
        )


class TestGetUserByIdBodyFiltering:
    def test_get_user_by_id_omits_privileged_fields(self, client) -> None:
        """GET /users/{user_id}: body has no is_superuser/tenant_id."""
        mock_service = Mock(spec=UserService)
        mock_service.get_profile = AsyncMock(return_value=_full_user_dict())

        client.app.dependency_overrides[users_router._get_user_service] = lambda: mock_service

        try:
            response = client.get("/api/v1/users/1", headers={"X-Tenant-ID": "1"})
        finally:
            client.app.dependency_overrides.pop(users_router._get_user_service, None)

        assert response.status_code == 200, response.text
        body = response.json()
        assert PUBLIC_FIELDS.issubset(body.keys())
        assert PRIVILEGED_FIELDS.isdisjoint(body.keys()), (
            f"privileged fields leaked in /users/{{user_id}} body: {PRIVILEGED_FIELDS & body.keys()}"
        )
