"""Unit tests for TaskService business logic.

Tests cover:
- Task creation with queue availability checks
- Task retrieval
- Queue unavailability handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_job():
    """Mock ARQ job."""
    job = MagicMock()
    job.job_id = "test-job-123"
    return job


@pytest.fixture
def mock_job_info():
    """Mock job info data."""
    info = MagicMock()
    info.__dict__ = {
        "job_id": "test-job-123",
        "function": "sample_background_task",
        "args": ("test message",),
        "kwargs": {},
        "enqueue_time": "2025-01-01T00:00:00Z",
        "score": None,
    }
    return info


class TestTaskServiceCreate:
    """Tests for task creation."""

    @pytest.mark.asyncio
    async def test_create_task_success(self, mock_job):
        """Test successful task creation."""
        from src.app.modules.tasks.service import task_service

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock(return_value=mock_job)

        with patch("src.app.modules.tasks.service.queue") as mock_queue:
            mock_queue.pool = mock_pool

            result = await task_service.create_task("test message")

            assert result["id"] == "test-job-123"
            mock_pool.enqueue_job.assert_called_once_with("sample_background_task", "test message")

    @pytest.mark.asyncio
    async def test_create_task_queue_unavailable(self):
        """Test task creation when queue is unavailable."""
        from fastapi import HTTPException

        from src.app.modules.tasks.service import task_service

        with patch("src.app.modules.tasks.service.queue") as mock_queue:
            mock_queue.pool = None

            with pytest.raises(HTTPException) as exc_info:
                await task_service.create_task("test message")

            assert exc_info.value.status_code == 503
            assert "Queue is not available" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_create_task_enqueue_fails(self):
        """Test task creation when enqueue returns None."""
        from fastapi import HTTPException

        from src.app.modules.tasks.service import task_service

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock(return_value=None)

        with patch("src.app.modules.tasks.service.queue") as mock_queue:
            mock_queue.pool = mock_pool

            with pytest.raises(HTTPException) as exc_info:
                await task_service.create_task("test message")

            assert exc_info.value.status_code == 500
            assert "Failed to create task" in str(exc_info.value.detail)


class TestTaskServiceGet:
    """Tests for task retrieval."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, mock_job_info):
        """Test successful task retrieval."""
        from src.app.modules.tasks.service import task_service

        mock_pool = MagicMock()

        with (
            patch("src.app.modules.tasks.service.queue") as mock_queue,
            patch("src.app.modules.tasks.service.ArqJob") as MockArqJob,
        ):
            mock_queue.pool = mock_pool
            mock_arq_job = AsyncMock()
            mock_arq_job.info = AsyncMock(return_value=mock_job_info)
            MockArqJob.return_value = mock_arq_job

            result = await task_service.get_task("test-job-123")

            assert result["job_id"] == "test-job-123"
            assert result["function"] == "sample_background_task"
            MockArqJob.assert_called_once_with("test-job-123", mock_pool)

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        """Test task retrieval when task doesn't exist."""
        from src.app.modules.tasks.service import task_service

        mock_pool = MagicMock()

        with (
            patch("src.app.modules.tasks.service.queue") as mock_queue,
            patch("src.app.modules.tasks.service.ArqJob") as MockArqJob,
        ):
            mock_queue.pool = mock_pool
            mock_arq_job = AsyncMock()
            mock_arq_job.info = AsyncMock(return_value=None)
            MockArqJob.return_value = mock_arq_job

            result = await task_service.get_task("nonexistent-job")

            assert result is None

    @pytest.mark.asyncio
    async def test_get_task_queue_unavailable(self):
        """Test task retrieval when queue is unavailable."""
        from fastapi import HTTPException

        from src.app.modules.tasks.service import task_service

        with patch("src.app.modules.tasks.service.queue") as mock_queue:
            mock_queue.pool = None

            with pytest.raises(HTTPException) as exc_info:
                await task_service.get_task("test-job-123")

            assert exc_info.value.status_code == 503
