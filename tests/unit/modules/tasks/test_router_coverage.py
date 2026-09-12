"""Unit tests for Tasks router (modules/tasks/router.py)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.api.deps.request_context import RequestContext
from src.app.core.tenancy.context import TenantContext
from src.app.modules.tasks import router as tasks_router
from src.app.modules.tasks.schemas import Job, TaskStatus
from src.app.modules.tasks.service import QueueUnavailableError, task_service
from src.app.shared.exceptions import ModelNotFoundError


def _make_ctx(tenant_id=None):
    """Build a RequestContext with a real TenantContext (or none)."""
    tenant = TenantContext(
        tenant_id=tenant_id or uuid4(),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )
    return RequestContext(user=MagicMock(id=1), tenant=tenant, db=MagicMock())


@pytest.fixture
def ctx():
    return _make_ctx()


async def test_create_task_success(ctx):
    with patch.object(task_service, "create_task", AsyncMock(return_value=Job(id="job-1"))):
        result = await tasks_router.create_task(body=MagicMock(message="hello"), ctx=ctx)
    assert result.id == "job-1"


async def test_create_task_queue_unavailable(ctx):
    with patch.object(task_service, "create_task", AsyncMock(side_effect=QueueUnavailableError())):
        # P1-19: router no longer converts — the domain error propagates to
        # the central EXCEPTION_HANDLERS table (503).
        with pytest.raises(QueueUnavailableError):
            await tasks_router.create_task(body=MagicMock(message="hello"), ctx=ctx)


async def test_create_task_value_error(ctx):
    with patch.object(task_service, "create_task", AsyncMock(side_effect=ValueError("boom"))):
        # P1-19: the ValueError→500 converter is gone — unexpected errors
        # propagate to the global catch-all (500).
        with pytest.raises(ValueError, match="boom"):
            await tasks_router.create_task(body=MagicMock(message="hello"), ctx=ctx)


async def test_create_task_scopes_tenant_from_ctx(ctx):
    """The tenant id passed to the service must come from the request context."""
    captured = {}

    async def fake_create_task(data, tenant_id=None):
        captured["data"] = data
        captured["tenant_id"] = tenant_id
        return Job(id="job-1")

    with patch.object(task_service, "create_task", side_effect=fake_create_task):
        await tasks_router.create_task(body=MagicMock(message="hello"), ctx=ctx)
    assert captured["tenant_id"] == ctx.tenant_id


async def test_get_task_success(ctx):
    with patch.object(
        task_service, "get_task", AsyncMock(return_value=TaskStatus(id="job-1", status="queued"))
    ):
        result = await tasks_router.get_task(task_id="job-1", ctx=ctx)
    assert result.id == "job-1"


async def test_get_task_not_found(ctx):
    with patch.object(task_service, "get_task", AsyncMock(return_value=None)):
        with pytest.raises(ModelNotFoundError, match="Task"):
            await tasks_router.get_task(task_id="job-1", ctx=ctx)


async def test_get_task_queue_unavailable(ctx):
    with patch.object(task_service, "get_task", AsyncMock(side_effect=QueueUnavailableError())):
        # P1-19: router no longer converts — propagates to the central table (503).
        with pytest.raises(QueueUnavailableError):
            await tasks_router.get_task(task_id="job-1", ctx=ctx)
