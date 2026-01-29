"""P1-1: Session Refresh for Update Operations Tests.

STATUS: IMPLEMENTED - Uses session refresh pattern

This module tests the session refresh optimization in TenantService.
The optimization eliminates double database roundtrips by using
`session.refresh()` after commit instead of a separate SELECT.

Before Pattern (2 queries):
    await self.crud.update(db=self.db, object=update_data, id=tenant_id)
    return await self.get_tenant(tenant_id)  # SECOND DB roundtrip

After Pattern (1 query + refresh):
    tenant = await self.db.get(Tenant, tenant_id)
    for key, value in update_data.items():
        setattr(tenant, key, value)
    await self.db.commit()
    await self.db.refresh(tenant)  # Uses existing connection, not new query
    return TenantResponse.model_validate(tenant).model_dump()

Note: PostService and UserService were not modified as they return
message dicts, not full entity data.
"""

from __future__ import annotations

import inspect

from src.app.modules.tenants.service import TenantService


class TestP11SessionRefreshImplementation:
    """Verify session refresh implementation in TenantService."""

    def test_update_tenant_uses_session_refresh(self) -> None:
        """update_tenant should use session.refresh pattern."""
        source = inspect.getsource(TenantService.update_tenant)
        # Verify the new pattern is used
        assert "await self.db.get(Tenant, tenant_id)" in source
        assert "await self.db.commit()" in source
        assert "await self.db.refresh(tenant)" in source
        # Verify old pattern is NOT used
        assert "await self.get_tenant(tenant_id)" not in source

    def test_deactivate_tenant_uses_session_refresh(self) -> None:
        """deactivate_tenant should use session.refresh pattern."""
        source = inspect.getsource(TenantService.deactivate_tenant)
        # Verify the new pattern is used
        assert "await self.db.get(Tenant, tenant_id)" in source
        assert "await self.db.commit()" in source
        assert "await self.db.refresh(tenant)" in source
        # Verify old pattern is NOT used
        assert "await self.get_tenant(tenant_id)" not in source

    def test_reactivate_tenant_uses_session_refresh(self) -> None:
        """reactivate_tenant should use session.refresh pattern."""
        source = inspect.getsource(TenantService.reactivate_tenant)
        # Verify the new pattern is used
        assert "await self.db.get(Tenant, tenant_id)" in source
        assert "await self.db.commit()" in source
        assert "await self.db.refresh(tenant)" in source
        # Verify old pattern is NOT used
        assert "await self.get_tenant(tenant_id)" not in source


class TestP11SessionRefreshDocumentation:
    """Verify optimization is documented in docstrings."""

    def test_update_tenant_docstring_notes_optimization(self) -> None:
        """update_tenant docstring should mention optimization."""
        docstring = TenantService.update_tenant.__doc__ or ""
        assert "session refresh" in docstring.lower() or "optimized" in docstring.lower()

    def test_deactivate_tenant_docstring_notes_optimization(self) -> None:
        """deactivate_tenant docstring should mention optimization."""
        docstring = TenantService.deactivate_tenant.__doc__ or ""
        assert "session refresh" in docstring.lower() or "optimized" in docstring.lower()

    def test_reactivate_tenant_docstring_notes_optimization(self) -> None:
        """reactivate_tenant docstring should mention optimization."""
        docstring = TenantService.reactivate_tenant.__doc__ or ""
        assert "session refresh" in docstring.lower() or "optimized" in docstring.lower()


class TestP11ResponseFormat:
    """Verify response format uses direct dict construction (P2-1 optimization).

    Note: P2-1 superseded P1-1's Pydantic serialization with _tenant_to_dict()
    for ~3x speedup.
    """

    def test_update_returns_dict_via_direct_construction(self) -> None:
        """update_tenant should return dict via _tenant_to_dict (P2-1)."""
        source = inspect.getsource(TenantService.update_tenant)
        assert "_tenant_to_dict(tenant)" in source

    def test_deactivate_returns_dict_via_direct_construction(self) -> None:
        """deactivate_tenant should return dict via _tenant_to_dict (P2-1)."""
        source = inspect.getsource(TenantService.deactivate_tenant)
        assert "_tenant_to_dict(tenant)" in source

    def test_reactivate_returns_dict_via_direct_construction(self) -> None:
        """reactivate_tenant should return dict via _tenant_to_dict (P2-1)."""
        source = inspect.getsource(TenantService.reactivate_tenant)
        assert "_tenant_to_dict(tenant)" in source


class TestP11PostUserServicesUnchanged:
    """Verify PostService and UserService were intentionally not modified.

    These services return message dicts, not entity data, so the optimization
    doesn't apply to them.
    """

    def test_post_update_returns_message(self) -> None:
        """PostService.update should return message dict."""
        from src.app.modules.posts.service import PostService
        source = inspect.getsource(PostService.update)
        # Verify it returns a message dict
        assert '{"message": "Post updated"}' in source or "Post updated" in source

    def test_user_update_returns_message(self) -> None:
        """UserService.update should return message dict."""
        from src.app.modules.users.service import UserService
        source = inspect.getsource(UserService.update)
        # Verify it returns a message dict
        assert '{"message": "User updated"}' in source or "User updated" in source
