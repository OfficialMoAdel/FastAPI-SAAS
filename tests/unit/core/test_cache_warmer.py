"""Unit tests for Cache Warmer."""

import asyncio
from unittest.mock import MagicMock, patch

from src.app.core.cache.warmer import CacheWarmer


class TestCacheWarmerRegister:
    """Test cache warmer registration decorator."""

    async def test_register_decorator_adds_function(self):
        """Test that @warmer.register adds function to registry."""
        test_warmer = CacheWarmer()

        @test_warmer.register("test_warmer")
        async def dummy_warm(session):
            return 5

        assert "test_warmer" in test_warmer.list_warmers()
        assert test_warmer.has_warmer("test_warmer")

    async def test_register_overwrites_existing(self):
        """Test that registering same name overwrites existing warmer."""
        test_warmer = CacheWarmer()

        @test_warmer.register("tenant")
        async def first_warm(session):
            return 1

        @test_warmer.register("tenant")
        async def second_warm(session):
            return 2

        assert len(test_warmer.list_warmers()) == 1
        result = await test_warmer.warm_specific("tenant", MagicMock())
        assert result.tenants_cached == 2

    async def test_singleton_warmer_instance(self):
        """Test that warmer is a singleton instance."""
        from src.app.core.cache.warmer import warmer as warmer1
        from src.app.core.cache.warmer import warmer as warmer2

        assert warmer1 is warmer2


class TestWarmAllExecution:
    """Test warm_all execution."""

    async def test_warm_all_executes_all_warmers(self):
        """Test that warm_all executes all registered warmers."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("tenant")
        async def warm_tenants(session):
            return 10

        @test_warmer.register("tier")
        async def warm_tiers(session):
            return 5

        result = await test_warmer.warm_all(mock_session)

        assert result.tenants_cached == 10
        assert result.errors is None

    async def test_warm_all_handles_errors(self):
        """Test that errors in one warmer don't stop others."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("failing_warmer")
        async def failing_warm(session):
            raise ValueError("Test error")

        @test_warmer.register("working_warmer")
        async def working_warm(session):
            return 3

        result = await test_warmer.warm_all(mock_session)

        assert result.tenants_cached == 0
        assert result.errors is not None
        assert len(result.errors) == 1
        assert "failing_warmer" in result.errors[0]

    async def test_warm_all_handles_timeout(self):
        """Test that warm_all handles timeout errors."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("slow_warmer")
        async def slow_warm(session):
            await asyncio.sleep(5)
            return 1

        @test_warmer.register("fast_warmer")
        async def fast_warm(session):
            return 2

        result = await test_warmer.warm_all(mock_session, timeout=0.1)

        assert result.tenants_cached == 0
        assert result.errors is not None
        assert "slow_warmer" in result.errors[0]
        assert "Timeout" in result.errors[0]

    from unittest.mock import patch

    async def test_warm_all_logs_start_and_end(self):
        """Test that warm_all logs start and end messages."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("test")
        async def dummy_warm(session):
            return 1

        with patch("src.app.core.cache.warmer.logger") as mock_logger:
            await test_warmer.warm_all(mock_session)

        # Log calls: call_args_list is a list of calls. Each call is (args, kwargs).
        # args[0] is the log message template.
        events = [call.args[0] for call in mock_logger.info.call_args_list if call.args]
        assert any("cache_warming_started" == e for e in events), events
        assert any(e == "cache_warming_finished" for e in events), events

    async def test_warm_all_returns_empty_result_when_no_warmers(self):
        """Test that warm_all returns empty result when no warmers registered."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        result = await test_warmer.warm_all(mock_session)

        assert result.tenants_cached == 0
        assert result.errors is None


class TestWarmSpecific:
    """Test warm_specific execution."""

    async def test_warm_specific_executes_single_warmer(self):
        """Test that warm_specific executes only the specified warmer."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("tenant")
        async def warm_tenants(session):
            return 10

        @test_warmer.register("tier")
        async def warm_tiers(session):
            return 5

        result = await test_warmer.warm_specific("tenant", mock_session)

        assert result.tenants_cached == 10

    async def test_warm_specific_unknown_warmer(self):
        """Test that warm_specific returns error for unknown warmer."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        result = await test_warmer.warm_specific("unknown", mock_session)

        assert result.errors is not None
        assert "Unknown cache warmer" in result.errors[0]

    async def test_warm_specific_handles_error(self):
        """Test that warm_specific handles errors."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("failing")
        async def failing_warm(session):
            raise ValueError("Test error")

        result = await test_warmer.warm_specific("failing", mock_session)

        assert result.errors is not None
        assert "failing" in result.errors[0]


class TestListWarmers:
    """Test list_warmers functionality."""

    async def test_list_warmers_returns_registered_names(self):
        """Test that list_warmers returns list of registered warmer names."""
        test_warmer = CacheWarmer()

        @test_warmer.register("first")
        async def first_warm(session):
            return 1

        @test_warmer.register("second")
        async def second_warm(session):
            return 2

        warmers = test_warmer.list_warmers()

        assert len(warmers) == 2
        assert "first" in warmers
        assert "second" in warmers


class TestHasWarmer:
    """Test has_warmer functionality."""

    async def test_has_warmer_true_for_registered(self):
        """Test that has_warmer returns True for registered warmer."""
        test_warmer = CacheWarmer()

        @test_warmer.register("test")
        async def dummy_warm(session):
            return 1

        assert test_warmer.has_warmer("test")

    async def test_has_warmer_false_for_unregistered(self):
        """Test that has_warmer returns False for unregistered warmer."""
        test_warmer = CacheWarmer()

        assert not test_warmer.has_warmer("nonexistent")


class TestWarmingObservability:
    """Test cache warming observability."""

    from unittest.mock import patch

    async def test_warm_all_logs_item_count(self):
        """Test that warm_all logs item count for each warmer."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("tenant")
        async def warm_tenants(session):
            return 15

        with patch("src.app.core.cache.warmer.logger") as mock_logger:
            await test_warmer.warm_all(mock_session)

        successes = [
            c for c in mock_logger.info.call_args_list
            if c.args and c.args[0] == "warmer_success"
        ]
        assert any(c.kwargs.get("items") == 15 for c in successes), successes

    async def test_warm_all_logs_error_count(self):
        """Test that warm_all logs error count when errors occur."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("failing")
        async def failing_warm(session):
            raise ValueError("Test error")

        with patch("src.app.core.cache.warmer.logger") as mock_logger:
            await test_warmer.warm_all(mock_session)

        completes = [
            c for c in mock_logger.info.call_args_list
            if c.args and c.args[0] == "cache_warming_finished"
        ]
        assert any(c.kwargs.get("errors") == 1 for c in completes), completes

    async def test_warm_all_logs_start_time(self):
        """Test that warm_all logs start time."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("test")
        async def dummy_warm(session):
            return 1

        with patch("src.app.core.cache.warmer.logger") as mock_logger:
            await test_warmer.warm_all(mock_session)

        assert any(
            c.args and c.args[0] == "cache_warming_started"
            for c in mock_logger.info.call_args_list
        )

    async def test_warm_all_logs_end_time(self):
        """Test that warm_all logs end time."""
        test_warmer = CacheWarmer()
        mock_session = MagicMock()

        @test_warmer.register("test")
        async def dummy_warm(session):
            return 1

        with patch("src.app.core.cache.warmer.logger") as mock_logger:
            await test_warmer.warm_all(mock_session)

        assert any(
            c.args and c.args[0] == "cache_warming_finished"
            for c in mock_logger.info.call_args_list
        )


def test_p24_no_tier_warmer_registered() -> None:
    """P2-4: the dead tier:name writer was removed — no 'tier' in the registry."""
    from src.app.core.cache.warmer import warmer

    assert not warmer.has_warmer("tier")
    assert "tier" not in warmer.list_warmers()


def test_p24_main_does_not_double_warm() -> None:
    """P2-4: lifespan warms only the auth-critical blacklist; bulk warming
    lives in the setup.py background task (single warmer)."""
    import inspect

    from src.app import main

    source = inspect.getsource(main)
    assert "_warm_application_caches" not in source
    assert "warmer.warm_all" not in source
