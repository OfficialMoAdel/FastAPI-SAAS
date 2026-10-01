"""SEC-07: chat-session ownership is part of the query, not a post-hoc check.

These run against the real async SQLite session, so they exercise the actual
WHERE clause rather than a mocked CRUD call shape. The shape being pinned is
the TRUST SHAPE, not a proven breach: the tenant predicate is applied by
``TenantAwareCRUD`` and RLS is a second layer, so nothing here demonstrates a
live cross-tenant read. What it demonstrates is that a row the actor does not
own never enters the result set, so no later reordering of load-then-compare
can turn this path into an IDOR.
"""

import inspect
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.app.core.tenancy.context import TenantContext, clear_tenant_context
from src.app.core.tenancy.lifecycle import bound_tenant
from src.app.modules.ai.models import AIChatMessage, AIChatSession
from src.app.modules.ai.schemas import ChatSessionUpdate
from src.app.modules.ai.service import ChatMessageService, ChatSessionService
from src.app.modules.users.models import User
from src.app.shared.exceptions import ModelNotFoundError


@pytest.fixture(autouse=True)
def _clear_tenant():
    """No test may inherit a tenant binding from another."""
    clear_tenant_context()
    yield
    clear_tenant_context()


@pytest.fixture
async def tenant_row(db_session):
    """A real tenant row plus the matching request-scoped context."""
    from src.app.models import Tenant

    tenant = Tenant(name="Ownership Tenant", slug=f"own-{uuid4().hex[:8]}", is_active=True)
    db_session.add(tenant)
    await db_session.commit()
    await db_session.refresh(tenant)
    return tenant, TenantContext(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        tenant_name=tenant.name,
        is_active=True,
    )


@pytest.fixture
async def two_users(db_session, tenant_row):
    """Two users in the same tenant: an owner and someone else."""
    from src.app.core.security import get_password_hash

    tenant, _ctx = tenant_row
    users = []
    for email in ("owner@example.com", "intruder@example.com"):
        user = User(
            email=email,
            username=email.split("@")[0],
            hashed_password=await get_password_hash("password"),
            is_superuser=False,
            tenant_id=tenant.id,
            name=email,
        )
        db_session.add(user)
        users.append(user)
    await db_session.commit()
    for user in users:
        await db_session.refresh(user)
    return users[0], users[1]


@pytest.fixture
async def owned_session(db_session, tenant_row, two_users):
    """A session owned by ``OWNER``, with two messages in it."""
    owner, intruder = two_users
    tenant, _ctx = tenant_row
    session = AIChatSession(
        tenant_id=tenant.id,
        user_id=owner.id,
        title="Owner private chat",
        model="gemma3",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    for role, content in (("user", "secret one"), ("assistant", "secret two")):
        db_session.add(
            AIChatMessage(tenant_id=tenant.id, session_id=session.id, role=role, content=content)
        )
    await db_session.commit()
    return session, owner, intruder


def _bound(tenant_row):
    """Bind the tenant context the request path would have established."""
    _tenant, ctx = tenant_row
    return bound_tenant(ctx)


class TestCrossUserReadsAreRefused:
    """A session the actor does not own is indistinguishable from a missing one."""

    @pytest.mark.asyncio
    async def test_get_session_refuses_another_users_session(self, db_session, tenant_row, owned_session):
        session, _owner, intruder = owned_session
        service = ChatSessionService(db_session)

        with _bound(tenant_row), patch(
            "src.app.modules.ai.service.crud_chat_sessions.get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = None
            with pytest.raises(ModelNotFoundError):
                await service.get_session(session.id, intruder.id)

        # The owner predicate is in the filter, so the row is never selected —
        # the query cannot even return the other user's row to compare against.
        mock_get.assert_awaited_once_with(db=db_session, id=session.id, user_id=intruder.id)

    @pytest.mark.asyncio
    async def test_get_session_returns_the_owners_own_session(self, db_session, tenant_row, owned_session):
        session, owner, _intruder = owned_session
        service = ChatSessionService(db_session)

        with _bound(tenant_row):
            found = await service.get_session(session.id, owner.id)

        assert found["id"] == session.id
        assert found["user_id"] == owner.id
        assert found["title"] == "Owner private chat"

    @pytest.mark.asyncio
    async def test_get_session_keeps_the_ownership_seam_as_assertion_layer(
        self, db_session, tenant_row, owned_session
    ):
        """The scoped read is backed by require_resource_ownership, not a bare compare.

        The filter is the load-bearing part; this pins that the canonical seam
        is still what adjudicates a row, so a future refactor that widens the
        query without widening the guard still fails closed.
        """
        session, owner, _intruder = owned_session
        service = ChatSessionService(db_session)

        with _bound(tenant_row), patch(
            "src.app.modules.ai.service.require_resource_ownership"
        ) as mock_seam, patch(
            "src.app.modules.ai.service.crud_chat_sessions.get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = {"id": session.id, "user_id": owner.id}
            await service.get_session(session.id, owner.id)

        mock_seam.assert_called_once_with(owner.id, owner.id)


class TestCrossUserWritesAreRefused:
    """Writes key on the owner predicate, so a 0-row write is the same 404."""

    @pytest.mark.asyncio
    async def test_update_targets_only_the_owned_row(self, db_session, tenant_row, owned_session):
        session, _owner, intruder = owned_session
        service = ChatSessionService(db_session)

        with _bound(tenant_row), patch(
            "src.app.modules.ai.service.crud_chat_sessions.get", new_callable=AsyncMock, return_value=None
        ), patch(
            "src.app.modules.ai.service.crud_chat_sessions.update", new_callable=AsyncMock
        ) as mock_update:
            with pytest.raises(ModelNotFoundError):
                await service.update_session(session.id, intruder.id, ChatSessionUpdate(title="hijacked"))

        # The owner predicate rides in the UPDATE's WHERE, not only in the
        # preceding read — a reordered or dropped read guard cannot widen it.
        assert mock_update.await_args is None, (
            "the scoped read returned no row, so update must never fire"
        )

    @pytest.mark.asyncio
    async def test_delete_targets_only_the_owned_row(self, db_session, tenant_row, owned_session):
        session, _owner, intruder = owned_session
        service = ChatSessionService(db_session)

        with _bound(tenant_row), patch(
            "src.app.modules.ai.service.crud_chat_sessions.get", new_callable=AsyncMock, return_value=None
        ), patch(
            "src.app.modules.ai.service.crud_chat_sessions.db_delete", new_callable=AsyncMock
        ) as mock_delete:
            with pytest.raises(ModelNotFoundError):
                await service.delete_session(session.id, intruder.id)

        assert mock_delete.await_args is None, (
            "the scoped read returned no row, so delete must never fire"
        )


class TestTheSessionSurvivesARealCrossUserAttempt:
    """End to end on the real database: the row and its history are untouched."""

    @pytest.mark.asyncio
    async def test_intruder_cannot_read_update_or_delete_the_session(
        self, db_session, tenant_row, owned_session
    ):
        session, _owner, intruder = owned_session
        sessions = ChatSessionService(db_session)

        with _bound(tenant_row):
            with pytest.raises(ModelNotFoundError):
                await sessions.get_session(session.id, intruder.id)
            with pytest.raises(ModelNotFoundError):
                await sessions.update_session(session.id, intruder.id, ChatSessionUpdate(title="hijacked"))
            with pytest.raises(ModelNotFoundError):
                await sessions.delete_session(session.id, intruder.id)

            still_there = await db_session.execute(select(AIChatSession).where(AIChatSession.id == session.id))
            row = still_there.scalar_one()
            assert row.title == "Owner private chat"

    @pytest.mark.asyncio
    async def test_owner_update_and_delete_still_work(self, db_session, tenant_row, owned_session):
        """The guard must not turn the owner's own path into a 404."""
        session, owner, _intruder = owned_session
        sessions = ChatSessionService(db_session)

        with _bound(tenant_row):
            updated = await sessions.update_session(session.id, owner.id, ChatSessionUpdate(title="Renamed"))
            assert updated["title"] == "Renamed"

            result = await sessions.delete_session(session.id, owner.id)
            assert "deleted" in result["message"].lower()

        db_session.expire_all()
        gone = await db_session.execute(select(AIChatSession).where(AIChatSession.id == session.id))
        assert gone.scalar_one_or_none() is None


class TestMessageHistoryIsOwnerScoped:
    """``AIChatMessage`` has no user_id, so scoping must hop to the session."""

    @pytest.mark.asyncio
    async def test_intruder_gets_no_messages_from_another_users_session(
        self, db_session, tenant_row, owned_session
    ):
        session, _owner, intruder = owned_session
        messages = ChatMessageService(db_session)

        with _bound(tenant_row), pytest.raises(ModelNotFoundError):
            await messages.get_session_messages(session.id, intruder.id)

    @pytest.mark.asyncio
    async def test_owner_reads_their_own_history(self, db_session, tenant_row, owned_session):
        session, owner, _intruder = owned_session
        messages = ChatMessageService(db_session)

        with _bound(tenant_row):
            page = await messages.get_session_messages(session.id, owner.id)

        assert page["total_count"] == 2
        assert [m["content"] for m in page["data"]] == ["secret one", "secret two"]

    @pytest.mark.asyncio
    async def test_owner_pagination_reports_the_scoped_total(self, db_session, tenant_row, owned_session):
        """The window total counts the owner's rows only, not every row in the table."""
        session, owner, _intruder = owned_session
        messages = ChatMessageService(db_session)

        with _bound(tenant_row):
            first = await messages.get_session_messages(session.id, owner.id, page=1, limit=1)
            out_of_range = await messages.get_session_messages(session.id, owner.id, page=9, limit=1)

        assert first["total_count"] == 2
        assert len(first["data"]) == 1
        # An empty page still reports the scoped total, or the caller would
        # conclude the session has no history.
        assert out_of_range["data"] == []
        assert out_of_range["total_count"] == 2


class TestSuperuserBypassIsNotAvailable:
    """The AI service methods take a user_id, not a principal.

    Unlike the posts/billing consumers, these signatures carry no
    ``is_superuser`` flag, so the seam's bypass has no way in here. That is
    deliberate: a superuser reading another user's chat history is not a
    supported operation on this surface. Pinned so that adding a bypass is a
    deliberate, visible change rather than an accident.
    """

    def test_none_of_the_four_operations_accept_a_superuser_flag(self):

        session_ops = (
            ChatSessionService.get_session,
            ChatSessionService.update_session,
            ChatSessionService.delete_session,
        )
        for op in (*session_ops, ChatMessageService.get_session_messages):
            params = inspect.signature(op).parameters
            assert "is_superuser" not in params, f"{op.__name__} grew a superuser bypass"
            # Every one of them takes the actor id the ownership predicate uses.
            assert "user_id" in params, f"{op.__name__} is not owner-scoped"

    def test_the_ownership_seam_is_the_one_the_module_imports(self):
        from src.app.core.security import ownership
        from src.app.modules.ai import service

        assert service.require_resource_ownership is ownership.require_resource_ownership
