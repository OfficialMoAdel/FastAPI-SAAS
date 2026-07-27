"""Contract tests for Unified Authorization Policy Framework.

Verifies RBACPolicy, OwnershipPolicy, composite policies (AllOf, AnyOf),
and the require_policy dependency factory to prevent authorization gaps and bypasses.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import Request

from src.app.core.exceptions.http_exceptions import ForbiddenException
from src.app.core.rbac.policy import AllOf, AnyOf, OwnershipPolicy, RBACPolicy
from src.app.core.rbac.require_policy import require_policy

MOCK_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class MockResource:
    """Mock resource model for ownership checks."""

    def __init__(self, id: int, user_id: int):
        self.id = id
        self.user_id = user_id


class TestAuthzPolicyAudit:
    """Audit tests for authorization policy evaluation and enforcement."""

    @pytest.mark.asyncio
    async def test_rbac_policy_superuser_bypass(self) -> None:
        """RBACPolicy allows superusers without checking DB permissions."""
        policy = RBACPolicy(permission="users:manage", allow_superuser=True)
        user = {"id": 1, "is_superuser": True, "tenant_id": MOCK_TENANT_ID}
        assert await policy.evaluate(request=None, user=user, db=None) is True

    @pytest.mark.asyncio
    async def test_rbac_policy_evaluation(self) -> None:
        """RBACPolicy evaluates user permissions against cached bitmask."""
        policy = RBACPolicy(permission="posts:write", allow_superuser=False)
        user = {"id": 2, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        mock_db = MagicMock()

        # Case 1: User has permission
        with patch("src.app.core.rbac.dependencies.has_permission_cached", new=AsyncMock(return_value=True)):
            assert await policy.evaluate(request=None, user=user, db=mock_db) is True

        # Case 2: User lacks permission
        with patch("src.app.core.rbac.dependencies.has_permission_cached", new=AsyncMock(return_value=False)):
            assert await policy.evaluate(request=None, user=user, db=mock_db) is False

    @pytest.mark.asyncio
    async def test_ownership_policy_direct_resource(self) -> None:
        """OwnershipPolicy verifies owner_field matches user id or grants admin bypass."""
        policy = OwnershipPolicy(owner_field="user_id", allow_superuser=True, admin_permission="posts:manage")
        owner_user = {"id": 10, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        other_user = {"id": 20, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        superuser = {"id": 30, "is_superuser": True, "tenant_id": MOCK_TENANT_ID}

        resource_obj = MockResource(id=1, user_id=10)
        resource_dict = {"id": 1, "user_id": 10}

        # Case 1: Owner accessing object or dict
        assert await policy.evaluate(request=None, user=owner_user, resource=resource_obj) is True
        assert await policy.evaluate(request=None, user=owner_user, resource=resource_dict) is True

        # Case 2: Non-owner accessing object without admin permission
        mock_db = MagicMock()
        with patch("src.app.core.rbac.dependencies.has_permission_cached", new=AsyncMock(return_value=False)):
            assert await policy.evaluate(request=None, user=other_user, resource=resource_obj, db=mock_db) is False

        # Case 3: Superuser bypass
        assert await policy.evaluate(request=None, user=superuser, resource=resource_obj) is True

        # Case 4: Admin permission bypass for non-owner
        with patch("src.app.core.rbac.dependencies.has_permission_cached", new=AsyncMock(return_value=True)):
            assert await policy.evaluate(request=None, user=other_user, resource=resource_obj, db=mock_db) is True

    @pytest.mark.asyncio
    async def test_ownership_policy_db_lookup_and_stash(self) -> None:
        """OwnershipPolicy fetches resource from DB via path param and stashes in request state."""
        policy = OwnershipPolicy(owner_field="user_id", model=MockResource, id_param="id")
        owner_user = {"id": 100, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}
        other_user = {"id": 200, "is_superuser": False, "tenant_id": MOCK_TENANT_ID}

        mock_request = MagicMock(spec=Request)
        mock_request.path_params = {"id": "55"}
        mock_request.state = MagicMock()
        mock_request.state.authorized_resource = None

        mock_record = MockResource(id=55, user_id=100)
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_record)

        # Case 1: Owner lookup from DB
        assert await policy.evaluate(request=mock_request, user=owner_user, db=mock_db) is True
        mock_db.get.assert_called_once_with(MockResource, 55)
        assert mock_request.state.authorized_resource == mock_record

        # Case 2: Stashed resource reuse without extra DB query
        mock_db.get.reset_mock()
        mock_request.state.authorized_resource = mock_record
        assert await policy.evaluate(request=mock_request, user=other_user, db=mock_db) is False
        mock_db.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_composite_policies(self) -> None:
        """Verify AllOf (logical AND) and AnyOf (logical OR) composite policies."""
        pass_policy = MagicMock()
        pass_policy.evaluate = AsyncMock(return_value=True)
        fail_policy = MagicMock()
        fail_policy.evaluate = AsyncMock(return_value=False)

        # AllOf test
        assert await AllOf(pass_policy, pass_policy).evaluate(None, {}) is True
        assert await AllOf(pass_policy, fail_policy).evaluate(None, {}) is False

        # AnyOf test
        assert await AnyOf(pass_policy, fail_policy).evaluate(None, {}) is True
        assert await AnyOf(fail_policy, fail_policy).evaluate(None, {}) is False

    @pytest.mark.asyncio
    async def test_require_policy_dependency_enforcement(self) -> None:
        """require_policy dependency raises ForbiddenException when policy fails."""
        pass_policy = MagicMock()
        pass_policy.evaluate = AsyncMock(return_value=True)
        fail_policy = MagicMock()
        fail_policy.evaluate = AsyncMock(return_value=False)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/v1/test"
        mock_user = {"id": 1}
        mock_db = MagicMock()

        # When policy passes, dependency checker returns None without exception
        checker_pass = require_policy(pass_policy)
        await checker_pass(request=mock_request, current_user=mock_user, db=mock_db)

        # When policy fails, dependency checker raises ForbiddenException (HTTP 403)
        checker_fail = require_policy(fail_policy)
        with pytest.raises(ForbiddenException) as exc_info:
            await checker_fail(request=mock_request, current_user=mock_user, db=mock_db)

        assert exc_info.value.status_code == 403
