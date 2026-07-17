"""Unit tests for Tasks router (modules/tasks/router.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.modules.tasks import router as tasks_router
from src.app.modules.tasks.service import QueueUnavailableError, task_service


class _User:
    def __init__(self, tenant_id="t-1"):
        self.tenant_id = tenant_id


@pytest.fixture
def current_user():
    return _User(tenant_id="t-1")


async def test_create_task_success(current_user):
    with patch.object(task_service, "create_task", AsyncMock(return_value={"id": "job-1"})):
        result = await tasks_router.create_task(body=MagicMock(message="hello"), current_user=current_user)
    assert result.id == "job-1"


async def test_create_task_queue_unavailable(current_user):
    with patch.object(task_service, "create_task", AsyncMock(side_effect=QueueUnavailableError())):
        with pytest.raises(HTTPException) as exc:
            await tasks_router.create_task(body=MagicMock(message="hello"), current_user=current_user)
    assert exc.value.status_code == 503


async def test_create_task_value_error(current_user):
    with patch.object(task_service, "create_task", AsyncMock(side_effect=ValueError("boom"))):
        with pytest.raises(HTTPException) as exc:
            await tasks_router.create_task(body=MagicMock(message="hello"), current_user=current_user)
    assert exc.value.status_code == 500


async def test_get_task_success(current_user):
    with patch.object(task_service, "get_task", AsyncMock(return_value={"id": "job-1", "status": "queued"})):
        result = await tasks_router.get_task(task_id="job-1", current_user=current_user)
    assert result.id == "job-1"


async def test_get_task_not_found(current_user):
    with patch.object(task_service, "get_task", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await tasks_router.get_task(task_id="job-1", current_user=current_user)
    assert exc.value.status_code == 404


async def test_get_task_queue_unavailable(current_user):
    with patch.object(task_service, "get_task", AsyncMock(side_effect=QueueUnavailableError())):
        with pytest.raises(HTTPException) as exc:
            await tasks_router.get_task(task_id="job-1", current_user=current_user)
    assert exc.value.status_code == 503
