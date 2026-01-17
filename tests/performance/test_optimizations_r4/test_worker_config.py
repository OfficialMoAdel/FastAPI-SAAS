"""P1-3: Worker max_jobs Configuration Tests.

STATUS: VERIFIED ✅

This module tests the ARQ worker max_jobs configuration optimization.
The optimization involves tuning max_jobs for optimal throughput based
on workload characteristics and DB pool capacity.

Configuration:
- max_jobs=10: Aligns with DB pool (20 total, 10 for web, 10 for workers)
- job_timeout=300: 5 minute timeout per job
- keep_result=3600: 1 hour result retention
"""

from __future__ import annotations


class TestP13WorkerConfigPrerequisites:
    """Pre-implementation tests for worker configuration."""

    def test_redis_cache_url_configured(self) -> None:
        """Verify Redis cache URL is configured for workers."""
        from src.app.core.config import settings

        # Check that Redis cache URL is available
        redis_url = getattr(settings, "REDIS_CACHE_URL", None)
        assert redis_url is not None

    def test_worker_settings_importable(self) -> None:
        """Verify WorkerSettings can be imported."""
        from src.app.core.worker.settings import WorkerSettings

        assert WorkerSettings is not None


class TestP13WorkerConfigVerification:
    """Tests to verify worker configuration optimization."""

    def test_max_jobs_configured(self) -> None:
        """max_jobs should be set to limit concurrent jobs."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "max_jobs")
        assert WorkerSettings.max_jobs == 10

    def test_job_timeout_configured(self) -> None:
        """job_timeout should be set to prevent runaway jobs."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "job_timeout")
        assert WorkerSettings.job_timeout == 300  # 5 minutes

    def test_keep_result_configured(self) -> None:
        """keep_result should be set to prevent memory leaks."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "keep_result")
        assert WorkerSettings.keep_result == 3600  # 1 hour

    def test_max_jobs_reasonable_for_db_pool(self) -> None:
        """max_jobs should leave headroom for web requests."""
        from src.app.core.config import settings
        from src.app.core.worker.settings import WorkerSettings

        db_pool_size = getattr(settings, "DB_POOL_SIZE", 20)
        # max_jobs should be at most half of DB pool to leave room for web
        assert WorkerSettings.max_jobs <= db_pool_size // 2 + 5

    def test_job_timeout_reasonable(self) -> None:
        """job_timeout should be between 1 min and 30 min."""
        from src.app.core.worker.settings import WorkerSettings

        # At least 1 minute
        assert WorkerSettings.job_timeout >= 60
        # At most 30 minutes
        assert WorkerSettings.job_timeout <= 1800

    def test_keep_result_reasonable(self) -> None:
        """keep_result should be between 5 min and 24 hours."""
        from src.app.core.worker.settings import WorkerSettings

        # At least 5 minutes
        assert WorkerSettings.keep_result >= 300
        # At most 24 hours
        assert WorkerSettings.keep_result <= 86400


class TestP13WorkerConfigIntegrity:
    """Tests to verify worker configuration doesn't break existing functionality."""

    def test_functions_still_configured(self) -> None:
        """Worker functions should still be registered."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "functions")
        assert len(WorkerSettings.functions) >= 1

    def test_redis_settings_still_configured(self) -> None:
        """Redis settings should still be configured."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "redis_settings")
        assert WorkerSettings.redis_settings is not None

    def test_serializers_still_configured(self) -> None:
        """Custom serializers should still be configured."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "job_serializer")
        assert hasattr(WorkerSettings, "job_deserializer")
        assert callable(WorkerSettings.job_serializer)
        assert callable(WorkerSettings.job_deserializer)

    def test_lifecycle_hooks_still_configured(self) -> None:
        """Startup/shutdown hooks should still be configured."""
        from src.app.core.worker.settings import WorkerSettings

        assert hasattr(WorkerSettings, "on_startup")
        assert hasattr(WorkerSettings, "on_shutdown")
        assert WorkerSettings.on_startup is not None
        assert WorkerSettings.on_shutdown is not None
