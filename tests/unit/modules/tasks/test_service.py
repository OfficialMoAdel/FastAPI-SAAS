"""Unit tests for TaskService business logic.

Tests cover:
- Task creation with queue availability checks
- Task retrieval
- Queue unavailability handling
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from src.app.modules.tasks.schemas import Job, TaskCreate


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

        with patch("src.app.modules.tasks.service.get_arq", return_value=mock_pool):

            result = await task_service.create_task(TaskCreate(message="test message"))

            assert result.id == "test-job-123"
            mock_pool.enqueue_job.assert_called_once_with(
                "sample_background_task", "test message", tenant_id=None, _job_id=ANY
            )

    @pytest.mark.asyncio
    async def test_create_task_queue_unavailable(self):
        """Test task creation when queue is unavailable."""
        from src.app.modules.tasks.service import QueueUnavailableError, task_service

        with patch("src.app.modules.tasks.service.get_arq", return_value=None):

            with pytest.raises(QueueUnavailableError) as exc_info:
                await task_service.create_task(TaskCreate(message="test message"))

            assert "Queue is not available" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_task_enqueue_returns_existing_job_id(self):
        """B7: enqueue_job returning None means the deterministic job id
        already exists — an idempotent re-create, not a failure. The service
        must return the SAME id instead of raising ValueError (old 500)."""
        import hashlib

        from src.app.modules.tasks.service import task_service

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock(return_value=None)

        with patch("src.app.modules.tasks.service.get_arq", return_value=mock_pool):

            result = await task_service.create_task(TaskCreate(message="test message"))

        # Service hashes f"{tenant_id}:{message}"; tenant_id=None renders as "None"
        expected_id = f"sample_task:{hashlib.sha256(b'None:test message').hexdigest()}"
        assert result == Job(id=expected_id)


class TestTaskServiceGet:
    """Tests for task retrieval."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, mock_job_info):
        """Test successful task retrieval."""
        from src.app.modules.tasks.service import task_service

        mock_pool = MagicMock()

        with (
            patch("src.app.modules.tasks.service.get_arq", return_value=mock_pool),
            patch("src.app.modules.tasks.service.ArqJob") as MockArqJob,
        ):
            mock_arq_job = AsyncMock()
            mock_arq_job.info = AsyncMock(return_value=mock_job_info)
            mock_arq_job.status = AsyncMock(return_value="queued")
            MockArqJob.return_value = mock_arq_job

            # get_task requires a UUID tenant_id; match the job's stored tenant_id for isolation
            tenant_id = UUID("11111111-1111-1111-1111-111111111111")
            mock_job_info.kwargs["tenant_id"] = str(tenant_id)
            result = await task_service.get_task("test-job-123", tenant_id=tenant_id)

            assert result is not None
            assert result.id == "test-job-123"
            assert result.status is not None
            MockArqJob.assert_called_once_with("test-job-123", mock_pool)

    @pytest.mark.asyncio
    async def test_get_task_not_found(self):
        """Test task retrieval when task doesn't exist."""
        from src.app.modules.tasks.service import task_service

        mock_pool = MagicMock()

        with (
            patch("src.app.modules.tasks.service.get_arq", return_value=mock_pool),
            patch("src.app.modules.tasks.service.ArqJob") as MockArqJob,
        ):
            mock_arq_job = AsyncMock()
            mock_arq_job.info = AsyncMock(return_value=None)
            MockArqJob.return_value = mock_arq_job

            result = await task_service.get_task(
                "nonexistent-job", tenant_id=UUID("11111111-1111-1111-1111-111111111111")
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_task_queue_unavailable(self):
        """Test task retrieval when queue is unavailable."""
        from src.app.modules.tasks.service import QueueUnavailableError, task_service

        with patch("src.app.modules.tasks.service.get_arq", return_value=None):

            with pytest.raises(QueueUnavailableError) as exc_info:
                await task_service.get_task(
                    "test-job-123", tenant_id=UUID("11111111-1111-1111-1111-111111111111")
                )

            assert "Queue is not available" in str(exc_info.value)
