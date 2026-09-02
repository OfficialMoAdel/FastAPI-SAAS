"""
Unit tests for R1-UNIFIED Infrastructure Components.

Tests the following components:
- LuaScriptManager: Script registration, preloading, EVALSHA/EVAL execution
- R1UnifiedMetrics: Counter/gauge/histogram operations, export
- R1 Error Handling: Fallback utilities, retry logic, error tracking
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.app.core.monitoring import (
    R1UnifiedMetrics,
    get_r1_metrics,
    record_event_idempotency,
    record_rbac_cache_hit,
    record_rbac_cache_miss,
    record_session_eviction,
    record_xfetch_latency,
    reset_r1_metrics,
    update_dlq_size,
)
from src.app.core.utils.lua_manager import (
    LuaScript,
    LuaScriptManager,
    ScriptNotFoundError,
    get_lua_manager,
    reset_lua_manager,
)
from src.app.core.utils.retry import (
    LuaScriptError,
    R1OptimizationError,
    with_fallback,
    with_fallback_sync,
    with_retry,
)

# =============================================================================
# LuaScriptManager Tests
# =============================================================================


class TestLuaScript:
    """Tests for LuaScript dataclass."""

    def test_sha1_calculation(self) -> None:
        """Test that SHA1 is calculated correctly."""
        script = LuaScript(name="test", source="return 1")
        assert len(script.sha1) == 40  # SHA1 is 40 hex chars
        # SHA1 of "return 1" - just verify it's consistent
        assert script.sha1 is not None

    def test_consistent_sha1(self) -> None:
        """Test that same source produces same SHA1."""
        script1 = LuaScript(name="test1", source="return 1")
        script2 = LuaScript(name="test2", source="return 1")
        assert script1.sha1 == script2.sha1

    def test_loaded_default_false(self) -> None:
        """Test that loaded defaults to False."""
        script = LuaScript(name="test", source="return 1")
        assert script.loaded is False


class TestLuaScriptManager:
    """Tests for LuaScriptManager."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_lua_manager()

    def test_register_script(self) -> None:
        """Test script registration."""
        manager = LuaScriptManager()
        script = manager.register("test_script", "return 1")

        assert script.name == "test_script"
        assert script.sha1 is not None
        assert "test_script" in manager.get_script_names()

    def test_get_script(self) -> None:
        """Test retrieving registered script."""
        manager = LuaScriptManager()
        manager.register("my_script", "return 42")

        script = manager.get_script("my_script")
        assert script.name == "my_script"

    def test_get_script_not_found(self) -> None:
        """Test ScriptNotFoundError for unknown script."""
        manager = LuaScriptManager()

        with pytest.raises(ScriptNotFoundError):
            manager.get_script("nonexistent")

    def test_builtin_scripts_registered(self) -> None:
        """Test that builtin scripts are registered on init."""
        manager = LuaScriptManager()
        names = manager.get_script_names()

        assert "idempotency_check" in names
        assert "xfetch_get_with_ttl" in names
        assert "dlq_bounded_push" in names
        # session_evict_oldest removed (dead code — zero callers; real
        # eviction happens inside session_register_with_eviction)

    def test_get_stats(self) -> None:
        """Test statistics retrieval."""
        manager = LuaScriptManager()
        stats = manager.get_stats()

        assert "total_scripts" in stats
        assert stats["total_scripts"] >= 5  # Builtin scripts
        assert "scripts" in stats

    @pytest.mark.asyncio
    async def test_preload_scripts(self) -> None:
        """Test script preloading."""
        manager = LuaScriptManager()
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="fake_sha")

        results = await manager.preload_scripts(mock_redis)

        assert all(results.values())  # All should succeed
        assert manager.is_preloaded()

    @pytest.mark.asyncio
    async def test_execute_with_evalsha(self) -> None:
        """Test execute uses EVALSHA."""
        manager = LuaScriptManager()
        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(return_value="result")

        result = await manager.execute(
            mock_redis,
            "idempotency_check",
            keys=["key1"],
            args=["300", "1"],
        )

        assert result == "result"
        mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_noscript_reloads_and_evalsha(self) -> None:
        """Test NOSCRIPT error triggers SCRIPT LOAD and EVALSHA retry."""
        manager = LuaScriptManager()
        mock_redis = AsyncMock()
        # First evalsha raises NOSCRIPT, second evalsha (after script_load) returns result
        mock_redis.evalsha = AsyncMock(
            side_effect=[
                Exception("NOSCRIPT No matching script"),
                "reloaded_result",
            ]
        )
        mock_redis.script_load = AsyncMock(return_value="fake_sha")

        result = await manager.execute(
            mock_redis,
            "idempotency_check",
            keys=["key1"],
            args=["300", "1"],
        )

        assert result == "reloaded_result"
        mock_redis.script_load.assert_called_once()
        assert mock_redis.evalsha.call_count == 2
        assert manager.get_script("idempotency_check").loaded is True

    @pytest.mark.asyncio
    async def test_execute_noscript_fallback_when_load_fails(self) -> None:
        """Test NOSCRIPT error falls back to EVAL when SCRIPT LOAD fails."""
        manager = LuaScriptManager()
        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(side_effect=Exception("NOSCRIPT No matching script"))
        mock_redis.script_load = AsyncMock(side_effect=Exception("SCRIPT LOAD failed"))
        mock_redis.eval = AsyncMock(return_value="fallback_result")

        result = await manager.execute(
            mock_redis,
            "idempotency_check",
            keys=["key1"],
            args=["300", "1"],
        )

        assert result == "fallback_result"
        mock_redis.script_load.assert_called_once()
        mock_redis.eval.assert_called_once()
        assert manager.get_script("idempotency_check").loaded is False

    @pytest.mark.asyncio
    async def test_execute_flush_recovery_flow(self) -> None:
        """Test recovery cycle: flush causes NOSCRIPT -> reloaded -> subsequent calls use EVALSHA."""
        manager = LuaScriptManager()
        mock_redis = AsyncMock()

        # Preload scripts initially
        mock_redis.script_load = AsyncMock(return_value="fake_sha")
        await manager.preload_scripts(mock_redis)
        assert manager.get_script("idempotency_check").loaded is True

        # Simulate Redis flush: next evalsha raises NOSCRIPT, reload succeeds, next evalsha succeeds
        mock_redis.evalsha = AsyncMock(
            side_effect=[
                Exception("NOSCRIPT No matching script. Please use EVAL."),
                "call1_result",
                "call2_result",
            ]
        )

        # Call 1 after flush
        res1 = await manager.execute(mock_redis, "idempotency_check", keys=["k1"], args=["1"])
        assert res1 == "call1_result"
        assert manager.get_script("idempotency_check").loaded is True

        # Call 2 should use EVALSHA directly (no exception raised on call 3 of side_effect)
        res2 = await manager.execute(mock_redis, "idempotency_check", keys=["k1"], args=["1"])
        assert res2 == "call2_result"
        assert manager.get_script("idempotency_check").loaded is True
        # Total evalsha calls: 1 (failed) + 1 (reloaded call 1) + 1 (call 2) = 3
        assert mock_redis.evalsha.call_count == 3
        mock_redis.eval.assert_not_called()
    def test_singleton(self) -> None:
        """Test singleton pattern."""
        manager1 = get_lua_manager()
        manager2 = get_lua_manager()
        assert manager1 is manager2


# =============================================================================
# R1UnifiedMetrics Tests
# =============================================================================


class TestR1UnifiedMetrics:
    """Tests for R1UnifiedMetrics."""

    def setup_method(self) -> None:
        """Reset metrics before each test."""
        reset_r1_metrics()

    def test_inc_counter(self) -> None:
        """Test counter increment."""
        metrics = R1UnifiedMetrics()
        metrics.inc_counter("r1_event_idempotency_checks_total")
        metrics.inc_counter("r1_event_idempotency_checks_total", 2)

        assert metrics.get_counter("r1_event_idempotency_checks_total") == 3

    def test_set_gauge(self) -> None:
        """Test gauge set."""
        metrics = R1UnifiedMetrics()
        metrics.set_gauge("r1_dlq_size", 100)

        assert metrics.get_gauge("r1_dlq_size") == 100

    def test_observe_histogram(self) -> None:
        """Test histogram observation."""
        metrics = R1UnifiedMetrics()
        metrics.observe_histogram("r1_xfetch_latency_seconds", 0.005)
        metrics.observe_histogram("r1_xfetch_latency_seconds", 0.015)

        hist = metrics.get_histogram("r1_xfetch_latency_seconds")
        assert hist["count"] == 2
        assert hist["sum"] == 0.020

    def test_convenience_functions(self) -> None:
        """Test convenience metric recording functions."""
        record_event_idempotency(is_duplicate=False)
        record_event_idempotency(is_duplicate=True)

        metrics = get_r1_metrics()
        assert metrics.get_counter("r1_event_idempotency_checks_total") == 2
        assert metrics.get_counter("r1_event_idempotency_duplicates_total") == 1

    def test_session_eviction_metrics(self) -> None:
        """Test session eviction recording."""
        record_session_eviction(roundtrips=5)

        metrics = get_r1_metrics()
        assert metrics.get_counter("r1_session_evictions_total") == 1
        hist = metrics.get_histogram("r1_session_eviction_roundtrips")
        assert hist["count"] == 1
        assert hist["sum"] == 5.0

    def test_xfetch_latency_metrics(self) -> None:
        """Test XFetch latency recording."""
        record_xfetch_latency(0.005, is_hit=True)
        record_xfetch_latency(0.010, is_hit=False)

        metrics = get_r1_metrics()
        assert metrics.get_counter("r1_xfetch_cache_hits_total") == 1
        assert metrics.get_counter("r1_xfetch_cache_misses_total") == 1

    def test_rbac_cache_metrics(self) -> None:
        """Test RBAC cache hit/miss recording."""
        record_rbac_cache_hit()
        record_rbac_cache_hit()
        record_rbac_cache_miss()

        metrics = get_r1_metrics()
        assert metrics.get_counter("r1_rbac_cache_hits_total") == 2
        assert metrics.get_counter("r1_rbac_cache_misses_total") == 1

    def test_dlq_metrics(self) -> None:
        """Test DLQ metrics."""
        update_dlq_size(500, max_size=1000)

        metrics = get_r1_metrics()
        assert metrics.get_gauge("r1_dlq_size") == 500
        assert metrics.get_gauge("r1_dlq_size_pct") == 50.0
    def test_export_prometheus(self) -> None:
        """Test Prometheus export format."""
        metrics = R1UnifiedMetrics()
        metrics.inc_counter("r1_event_idempotency_checks_total", 10)
        metrics.set_gauge("r1_dlq_size", 50)

        output = metrics.export_prometheus()

        assert "r1_event_idempotency_checks_total 10" in output
        assert "r1_dlq_size 50" in output
        assert "# TYPE" in output

    def test_reset(self) -> None:
        """Test metrics reset."""
        metrics = R1UnifiedMetrics()
        metrics.inc_counter("r1_event_idempotency_checks_total", 10)
        metrics.reset()

        assert metrics.get_counter("r1_event_idempotency_checks_total") == 0



# =============================================================================
# Error Handling Tests
# =============================================================================


class TestExceptionHierarchy:
    """Tests for exception classes."""

    def test_base_exception(self) -> None:
        """Test R1OptimizationError."""
        error = R1OptimizationError("test error", "test_opt")
        assert str(error) == "test error"
        assert error.optimization == "test_opt"
        assert error.timestamp > 0

    def test_lua_script_error(self) -> None:
        """Test LuaScriptError."""
        error = LuaScriptError("script failed", "my_script")
        assert error.script_name == "my_script"
        assert isinstance(error, R1OptimizationError)



class TestWithFallback:
    """Tests for with_fallback utility."""

    @pytest.mark.asyncio
    async def test_primary_success(self) -> None:
        """Test primary function success path."""

        async def primary() -> str:
            return "primary_result"

        async def fallback() -> str:
            return "fallback_result"

        result = await with_fallback(primary, fallback)
        assert result == "primary_result"

    @pytest.mark.asyncio
    async def test_fallback_on_error(self) -> None:
        """Test fallback executes on primary error."""

        async def primary() -> str:
            raise ValueError("primary failed")

        async def fallback() -> str:
            return "fallback_result"

        result = await with_fallback(primary, fallback, "Expected failure")
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_both_fail(self) -> None:
        """Test exception raised when both fail."""

        async def primary() -> str:
            raise ValueError("primary failed")

        async def fallback() -> str:
            raise RuntimeError("fallback also failed")

        with pytest.raises(RuntimeError, match="fallback also failed"):
            await with_fallback(primary, fallback)

    def test_sync_fallback(self) -> None:
        """Test synchronous fallback."""

        def primary() -> str:
            raise ValueError("error")

        def fallback() -> str:
            return "fallback"

        result = with_fallback_sync(primary, fallback)
        assert result == "fallback"


class TestWithRetry:
    """Tests for with_retry utility."""

    @pytest.mark.asyncio
    async def test_success_first_try(self) -> None:
        """Test success on first attempt."""
        call_count = 0

        async def operation() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = await with_retry(operation, max_attempts=3)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self) -> None:
        """Test success after initial failures."""
        call_count = 0

        async def operation() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "success"

        result = await with_retry(operation, max_attempts=3, delay_seconds=0.01)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_attempts_fail(self) -> None:
        """Test exception after all attempts exhausted."""

        async def operation() -> str:
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            await with_retry(operation, max_attempts=2, delay_seconds=0.01)



