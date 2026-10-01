"""UserTierUpdate / UserStatusUpdate must reject unknown fields.

Both schemas feed superuser-only privilege mutations
(PATCH /users/{user_id}/tier and /users/{user_id}/status). Pydantic's default
``extra="ignore"`` silently drops anything the schema does not declare, which
turns a typo (``{"tier": 3}``) or a mass-assignment attempt (``is_superuser``,
``tenant_id``) into a 200 response that looks like the write landed.
``extra="forbid"`` makes both a 422 instead.
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.app.modules.users.router import (
    get_current_superuser,
    get_user_service,
)
from src.app.modules.users.router import (
    router as users_router,
)
from src.app.modules.users.schemas import UserStatusUpdate, UserTierUpdate

PRIVILEGED_SMUGGLES = ["is_superuser", "tenant_id", "is_deleted", "token_version", "role"]


class _StubService:
    async def update_tier(self, _user_id: str, _values: UserTierUpdate) -> dict:
        return {"message": "tier updated"}

    async def set_user_status(self, _user_id: str, _is_active: bool) -> dict:
        return {"message": "status updated"}


def _make_client() -> TestClient:
    """App with only the users router; auth and service stubbed out."""
    app = FastAPI()
    app.include_router(users_router)

    async def _fake_superuser() -> dict:
        return {"id": 1, "username": "root", "is_superuser": True}

    app.dependency_overrides[get_current_superuser] = _fake_superuser
    app.dependency_overrides[get_user_service] = lambda: _StubService()
    return TestClient(app)


class TestUserTierUpdateSchema:
    def test_valid_payload_accepted(self):
        values = UserTierUpdate(tier_id=3)
        assert values.tier_id == 3
        assert values.model_dump() == {"tier_id": 3}

    def test_typo_field_rejected(self):
        """A misspelled tier_id would silently become a no-op write."""
        with pytest.raises(ValidationError) as exc_info:
            UserTierUpdate(tier=3)
        assert "tier_id" in str(exc_info.value)

    @pytest.mark.parametrize("field", PRIVILEGED_SMUGGLES)
    def test_privileged_extra_fields_rejected(self, field):
        with pytest.raises(ValidationError):
            UserTierUpdate(tier_id=3, **{field: "x"})

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            UserTierUpdate()


class TestUserStatusUpdateSchema:
    def test_valid_payload_accepted(self):
        values = UserStatusUpdate(is_active=False)
        assert values.is_active is False
        assert values.model_dump() == {"is_active": False}

    def test_typo_field_rejected(self):
        """A misspelled is_active would silently no-op a deactivation."""
        with pytest.raises(ValidationError) as exc_info:
            UserStatusUpdate(active=False)
        assert "is_active" in str(exc_info.value)

    @pytest.mark.parametrize("field", PRIVILEGED_SMUGGLES)
    def test_privileged_extra_fields_rejected(self, field):
        with pytest.raises(ValidationError):
            UserStatusUpdate(is_active=True, **{field: "x"})

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            UserStatusUpdate()


class TestRoutesRejectExtras:
    """Wire-level proof: the 422 comes from the route, not just the class."""

    def test_patch_tier_rejects_extra_field(self):
        response = _make_client().patch("/users/u1/tier", json={"tier_id": 2, "is_superuser": True})
        assert response.status_code == 422, response.text

    def test_patch_tier_rejects_typo(self):
        response = _make_client().patch("/users/u1/tier", json={"tier": 2})
        assert response.status_code == 422, response.text

    def test_patch_tier_accepts_valid_payload(self):
        response = _make_client().patch("/users/u1/tier", json={"tier_id": 2})
        assert response.status_code == 200, response.text

    def test_patch_status_rejects_extra_field(self):
        response = _make_client().patch("/users/u1/status", json={"is_active": False, "tenant_id": "other"})
        assert response.status_code == 422, response.text

    def test_patch_status_rejects_typo(self):
        response = _make_client().patch("/users/u1/status", json={"active": False})
        assert response.status_code == 422, response.text

    def test_patch_status_accepts_valid_payload(self):
        response = _make_client().patch("/users/u1/status", json={"is_active": False})
        assert response.status_code == 200, response.text

    def test_openapi_marks_bodies_additional_properties_false(self):
        """FastAPI surfaces extra='forbid' as additionalProperties: false."""
        components = _make_client().get("/openapi.json").json()["components"]["schemas"]
        for schema_name in ("UserTierUpdate", "UserStatusUpdate"):
            assert components[schema_name].get("additionalProperties") is False, schema_name


class TestOtherSchemasUntouched:
    def test_base_update_siblings_already_forbid_extras(self):
        """``BaseUpdate`` carries extra='forbid'; the two plain-BaseModel admin
        bodies did not — that gap is what this change closes."""
        from src.app.modules.users.schemas import UserDelete

        with pytest.raises(ValidationError):
            UserDelete(is_deleted=True, deleted_at=datetime.now(UTC), is_superuser=True)

    def test_sibling_default_schemas_still_ignore_extras(self):
        """Guard against a blanket config change on the module: plain
        BaseModel siblings keep Pydantic's default."""
        from src.app.modules.users.schemas import UserRestoreDeleted, UserTierResponse

        assert UserRestoreDeleted(is_deleted=False, extra_junk=1).is_deleted is False
        assert UserTierResponse(tier_id=1, extra_junk=1).tier_id == 1
