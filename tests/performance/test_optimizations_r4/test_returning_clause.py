"""P1-1: Session Refresh for Update Operations Tests.

STATUS: IMPLEMENTED - Uses session refresh pattern

This module tests the session refresh optimization in TenantService.
The optimization eliminates double database roundtrips by using
`session.refresh()` after commit instead of a separate SELECT.

WHAT IS ASSERTED, AND WHY NOT SOURCE TEXT
-----------------------------------------
An earlier revision of this file read ``inspect.getsource`` and asserted that
literal strings such as ``"await self.db.refresh(tenant)"`` appeared — and
that ``"await self.get_tenant(tenant_id)"`` did not. That form is wrong twice
over:

* It fails on harmless change. Renaming the ORM attribute, routing the refresh
  through a helper, or splitting the method would break a green behaviour.
* It passes on the defect. An implementation that re-introduced the second
  read would sail through, provided the forbidden line was not spelled exactly
  ``await self.get_tenant(tenant_id)``. A substring ban is trivially evaded by
  ``await self.get_tenant(tid)``.

The roundtrip set IS the optimization, so it is measured instead: a fake
session records every call, and the contract is the call sequence — one load,
one commit, one refresh, and no second read. That is refactor-agnostic and
catches the regression the substring check could not.

The Pydantic-bypass contract (P2-1) is likewise observed on the returned
payload: the result is a plain dict that is field-for-field what
``TenantResponse.model_validate(...)`` would have produced.

Note: PostService and UserService were not modified as they return message
dicts, not full entity data. Their message-dict contract is asserted
behaviourally in ``tests/unit/modules/posts/test_service.py`` and
``tests/unit/modules/users/test_user_service.py``; it is not re-pinned here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.modules.tenants.models import Tenant
from src.app.modules.tenants.schemas import TenantResponse, TenantUpdate
from src.app.modules.tenants.service import TenantService, _tenant_to_dict
from src.app.shared.exceptions import DuplicateError


def _orm_tenant(*, slug: str = "acme-corp", is_active: bool = True) -> MagicMock:
    """A Tenant ORM stand-in carrying only the attributes the service reads."""
    tenant = MagicMock(spec=Tenant)
    tenant.id = uuid.uuid4()
    tenant.name = "Acme Corporation"
    tenant.slug = slug
    tenant.is_active = is_active
    tenant.feature_flags = {}
    tenant.storage_usage_bytes = 0
    tenant.file_count = 0
    tenant.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    tenant.updated_at = None
    return tenant


@pytest.fixture
def service_and_session():
    """``(service, db, tenant)`` with cache side effects neutralised.

    ``_invalidate_tenant_cache`` / ``_cache_tenant`` are patched so these
    tests measure the DB roundtrips and the returned payload only, rather than
    whether a Redis double happens to be wired for this event loop.
    """

    def _build(*, slug: str = "acme-corp", is_active: bool = True):
        tenant = _orm_tenant(slug=slug, is_active=is_active)
        db = AsyncMock()
        db.get.return_value = tenant
        service = TenantService(db)
        return service, db, tenant

    with (
        patch.object(TenantService, "_invalidate_tenant_cache", new_callable=AsyncMock),
        patch.object(TenantService, "_cache_tenant", new_callable=AsyncMock),
    ):
        yield _build


def _assert_one_query_one_commit_one_refresh(db: AsyncMock, tenant: MagicMock) -> None:
    """The whole point of the optimization, as an observed call sequence.

    ``db.get`` is the only load the service may issue: a second call is the
    re-introduced SELECT, whether it arrives via ``db.get`` or through the CRUD
    layer. ``commit`` must precede ``refresh`` — a refresh before the commit
    would read pre-transaction state and silently drop the update.
    """
    assert db.get.await_count == 1, f"expected exactly one DB load, got {db.get.await_count}"
    assert db.commit.await_count == 1
    assert db.refresh.await_count == 1
    db.refresh.assert_awaited_once_with(tenant)

    commit_order = db.mock_calls.index(next(c for c in db.mock_calls if c[0] == "commit"))
    refresh_order = db.mock_calls.index(next(c for c in db.mock_calls if c[0] == "refresh"))
    assert commit_order < refresh_order, "refresh must follow the commit that made the update durable"


class TestP11SessionRefreshImplementation:
    """Verify session refresh implementation in TenantService."""

    @pytest.mark.asyncio
    async def test_update_tenant_uses_session_refresh(self, service_and_session) -> None:
        """update_tenant loads once, commits, refreshes, and reports the update."""
        service, db, tenant = service_and_session()

        result = await service.update_tenant(tenant.id, TenantUpdate(name="Acme Corp Updated"))

        _assert_one_query_one_commit_one_refresh(db, tenant)
        assert result["name"] == "Acme Corp Updated", "the refreshed payload must expose the applied update"
        assert result["updated_at"] is not None, "update_tenant stamps updated_at"
        assert result["slug"] == tenant.slug, "an unrelated field must survive the update"

    @pytest.mark.asyncio
    async def test_deactivate_tenant_uses_session_refresh(self, service_and_session) -> None:
        """deactivate_tenant loads once, commits, refreshes, and soft-deletes."""
        service, db, tenant = service_and_session()

        result = await service.deactivate_tenant(tenant.id)

        _assert_one_query_one_commit_one_refresh(db, tenant)
        assert tenant.is_active is False, "the ORM object must carry the soft delete"
        assert result["is_active"] is False

    @pytest.mark.asyncio
    async def test_reactivate_tenant_uses_session_refresh(self, service_and_session) -> None:
        """reactivate_tenant loads once, commits, refreshes, and re-activates."""
        service, db, tenant = service_and_session(is_active=False)

        result = await service.reactivate_tenant(tenant.id)

        _assert_one_query_one_commit_one_refresh(db, tenant)
        assert tenant.is_active is True
        assert result["is_active"] is True


class TestP11NoSecondReadThroughCrud:
    """The optimization's win, stated so no spelling can evade it.

    This is what ``assert "await self.get_tenant(tenant_id)" not in source``
    was reaching for. A renamed argument, an alias, or a differently-spelled
    call all satisfy the substring check while re-introducing the second
    read; none of them can satisfy this one.
    """

    @pytest.mark.asyncio
    async def test_update_tenant_does_not_re_read(self, service_and_session) -> None:
        """update_tenant issues no read beyond its initial load."""
        service, db, tenant = service_and_session()

        with patch.object(service, "get_tenant", new_callable=AsyncMock) as get_tenant:
            await service.update_tenant(tenant.id, TenantUpdate(name="Renamed"))

        get_tenant.assert_not_awaited()
        assert db.get.await_count == 1

    @pytest.mark.asyncio
    async def test_deactivate_tenant_does_not_re_read(self, service_and_session) -> None:
        """deactivate_tenant issues no read beyond its initial load."""
        service, db, tenant = service_and_session()

        with patch.object(service, "get_tenant", new_callable=AsyncMock) as get_tenant:
            await service.deactivate_tenant(tenant.id)

        get_tenant.assert_not_awaited()
        assert db.get.await_count == 1

    @pytest.mark.asyncio
    async def test_reactivate_tenant_does_not_re_read(self, service_and_session) -> None:
        """reactivate_tenant issues no read beyond its initial load."""
        service, db, tenant = service_and_session(is_active=False)

        with patch.object(service, "get_tenant", new_callable=AsyncMock) as get_tenant:
            await service.reactivate_tenant(tenant.id)

        get_tenant.assert_not_awaited()
        assert db.get.await_count == 1


class TestP11UpdateFailurePath:
    """A failed commit must not leave a half-applied tenant behind."""

    @pytest.mark.asyncio
    async def test_commit_failure_rolls_back_and_raises(self, service_and_session) -> None:
        """An IntegrityError on commit rolls back, skips the refresh, and raises."""
        service, db, tenant = service_and_session()
        db.commit.side_effect = IntegrityError("UPDATE tenants ...", {}, Exception("duplicate key value"))

        with pytest.raises(DuplicateError) as exc_info:
            await service.update_tenant(tenant.id, TenantUpdate(name="Clashing Slug"))

        db.rollback.assert_awaited_once()
        assert db.refresh.await_count == 0, "a rolled-back commit must not be followed by a refresh"
        assert exc_info.value.field == "slug"

    @pytest.mark.asyncio
    async def test_missing_tenant_raises_before_any_write(self, service_and_session) -> None:
        """A missing tenant fails fast — no commit, no refresh, no cache write."""
        from src.app.shared.exceptions import ModelNotFoundError

        service, db, _tenant = service_and_session()
        db.get.return_value = None

        with pytest.raises(ModelNotFoundError) as exc_info:
            await service.update_tenant(uuid.uuid4(), TenantUpdate(name="Nobody"))

        db.commit.assert_not_awaited()
        db.refresh.assert_not_awaited()
        assert exc_info.value.model_name == "Tenant"


class TestP11ResponseFormat:
    """Verify response format uses direct dict construction (P2-1 optimization).

    Note: P2-1 superseded P1-1's Pydantic serialization with _tenant_to_dict()
    for ~3x speedup. The bypass is asserted on the RETURNED payload — a plain
    dict, isomorphic to the Pydantic dump — rather than on a call name read
    out of the source.
    """

    @pytest.mark.asyncio
    async def test_update_returns_dict_via_direct_construction(self, service_and_session) -> None:
        """update_tenant returns a plain dict equal to the Pydantic dump."""
        service, _db, tenant = service_and_session()

        result = await service.update_tenant(tenant.id, TenantUpdate(name="Acme Corp Updated"))

        assert type(result) is dict, "P2-1 bypasses Pydantic; the result must be a plain dict"
        assert result == TenantResponse.model_validate(tenant).model_dump()

    @pytest.mark.asyncio
    async def test_deactivate_returns_dict_via_direct_construction(self, service_and_session) -> None:
        """deactivate_tenant returns a plain dict equal to the Pydantic dump."""
        service, _db, tenant = service_and_session()

        result = await service.deactivate_tenant(tenant.id)

        assert type(result) is dict
        assert result == TenantResponse.model_validate(tenant).model_dump()

    @pytest.mark.asyncio
    async def test_reactivate_returns_dict_via_direct_construction(self, service_and_session) -> None:
        """reactivate_tenant returns a plain dict equal to the Pydantic dump."""
        service, _db, tenant = service_and_session(is_active=False)

        result = await service.reactivate_tenant(tenant.id)

        assert type(result) is dict
        assert result == TenantResponse.model_validate(tenant).model_dump()

    @pytest.mark.asyncio
    async def test_returned_payload_is_the_helper_output(self, service_and_session) -> None:
        """The response is the shared helper's output, not a parallel build."""
        service, _db, tenant = service_and_session()

        result = await service.update_tenant(tenant.id, TenantUpdate(name="Shared Shape"))

        assert result == _tenant_to_dict(tenant)
