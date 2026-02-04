"""Unit tests for performance optimization feature flags.

Tests cover:
- Flag default values
- Environment variable override
- Flag accessibility via settings
"""

import os

import pytest

from src.app.core.config import PerformanceOptimizationSettings, Settings


class TestPerformanceOptimizationFlagsDefaults:
    """Tests for performance optimization flag default values."""

    def test_p0_flags_default_to_true(self) -> None:
        """P0 critical flags should default to True (safe for production)."""
        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_REMOVE_WASTED_CONNECTION is True
        assert perf.FEATURE_STREAMING_UPLOADS is True

    def test_p1_flags_default_to_true(self) -> None:
        """P1 high-priority flags should default to True."""
        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_CONTEXTVAR_OPTIMIZATION is True
        assert perf.FEATURE_BILLING_DICT_OPTIMIZATION is True
        assert perf.FEATURE_EAGER_LOADING is True
        assert perf.FEATURE_AUDIT_FLUSH_LIMIT is True

    def test_p2_p3_flags_default_to_false(self) -> None:
        """P2 and P3 experimental flags should default to False."""
        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_CIRCUIT_BREAKER is False
        assert perf.FEATURE_EVENT_RETRY_DLQ is False
        assert perf.FEATURE_ATOMIC_CACHE_INVALIDATION is False
        assert perf.FEATURE_WEBHOOK_CONNECTION_MGMT is False
        assert perf.FEATURE_SKIP_RLS_FOR_ADMIN is False


class TestPerformanceOptimizationFlagsEnvOverride:
    """Tests for environment variable override."""

    def test_feature_remove_wasted_connection_override(self) -> None:
        """Test FEATURE_REMOVE_WASTED_CONNECTION can be overridden via env."""
        os.environ["FEATURE_REMOVE_WASTED_CONNECTION"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_REMOVE_WASTED_CONNECTION is False

        os.environ.pop("FEATURE_REMOVE_WASTED_CONNECTION", None)

    def test_feature_streaming_uploads_override(self) -> None:
        """Test FEATURE_STREAMING_UPLOADS can be overridden via env."""
        os.environ["FEATURE_STREAMING_UPLOADS"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_STREAMING_UPLOADS is False

        os.environ.pop("FEATURE_STREAMING_UPLOADS", None)

    def test_feature_contextvar_optimization_override(self) -> None:
        """Test FEATURE_CONTEXTVAR_OPTIMIZATION can be overridden via env."""
        os.environ["FEATURE_CONTEXTVAR_OPTIMIZATION"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_CONTEXTVAR_OPTIMIZATION is False

        os.environ.pop("FEATURE_CONTEXTVAR_OPTIMIZATION", None)

    def test_feature_billing_dict_optimization_override(self) -> None:
        """Test FEATURE_BILLING_DICT_OPTIMIZATION can be overridden via env."""
        os.environ["FEATURE_BILLING_DICT_OPTIMIZATION"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_BILLING_DICT_OPTIMIZATION is False

        os.environ.pop("FEATURE_BILLING_DICT_OPTIMIZATION", None)

    def test_feature_eager_loading_override(self) -> None:
        """Test FEATURE_EAGER_LOADING can be overridden via env."""
        os.environ["FEATURE_EAGER_LOADING"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_EAGER_LOADING is False

        os.environ.pop("FEATURE_EAGER_LOADING", None)

    def test_feature_audit_flush_limit_override(self) -> None:
        """Test FEATURE_AUDIT_FLUSH_LIMIT can be overridden via env."""
        os.environ["FEATURE_AUDIT_FLUSH_LIMIT"] = "false"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_AUDIT_FLUSH_LIMIT is False

        os.environ.pop("FEATURE_AUDIT_FLUSH_LIMIT", None)

    def test_feature_circuit_breaker_override(self) -> None:
        """Test FEATURE_CIRCUIT_BREAKER can be overridden via env."""
        os.environ["FEATURE_CIRCUIT_BREAKER"] = "true"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_CIRCUIT_BREAKER is True

        os.environ.pop("FEATURE_CIRCUIT_BREAKER", None)

    def test_feature_event_retry_dlq_override(self) -> None:
        """Test FEATURE_EVENT_RETRY_DLQ can be overridden via env."""
        os.environ["FEATURE_EVENT_RETRY_DLQ"] = "true"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_EVENT_RETRY_DLQ is True

        os.environ.pop("FEATURE_EVENT_RETRY_DLQ", None)

    def test_feature_atomic_cache_invalidation_override(self) -> None:
        """Test FEATURE_ATOMIC_CACHE_INVALIDATION can be overridden via env."""
        os.environ["FEATURE_ATOMIC_CACHE_INVALIDATION"] = "true"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_ATOMIC_CACHE_INVALIDATION is True

        os.environ.pop("FEATURE_ATOMIC_CACHE_INVALIDATION", None)

    def test_feature_webhook_connection_mgmt_override(self) -> None:
        """Test FEATURE_WEBHOOK_CONNECTION_MGMT can be overridden via env."""
        os.environ["FEATURE_WEBHOOK_CONNECTION_MGMT"] = "true"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_WEBHOOK_CONNECTION_MGMT is True

        os.environ.pop("FEATURE_WEBHOOK_CONNECTION_MGMT", None)

    def test_feature_skip_rls_for_admin_override(self) -> None:
        """Test FEATURE_SKIP_RLS_FOR_ADMIN can be overridden via env."""
        os.environ["FEATURE_SKIP_RLS_FOR_ADMIN"] = "true"

        perf = PerformanceOptimizationSettings()

        assert perf.FEATURE_SKIP_RLS_FOR_ADMIN is True

        os.environ.pop("FEATURE_SKIP_RLS_FOR_ADMIN", None)


class TestPerformanceOptimizationSettingsIntegration:
    """Tests for performance settings integration with main Settings."""

    def test_settings_has_performance_property(self) -> None:
        """Test Settings object has performance property."""
        settings = Settings()

        assert hasattr(settings, "performance")
        assert isinstance(settings.performance, PerformanceOptimizationSettings)

    def test_settings_performance_flags_accessible(self) -> None:
        """Test all flags accessible via settings.performance."""
        settings = Settings()

        assert hasattr(settings.performance, "FEATURE_REMOVE_WASTED_CONNECTION")
        assert hasattr(settings.performance, "FEATURE_STREAMING_UPLOADS")
        assert hasattr(settings.performance, "FEATURE_CONTEXTVAR_OPTIMIZATION")
        assert hasattr(settings.performance, "FEATURE_BILLING_DICT_OPTIMIZATION")
        assert hasattr(settings.performance, "FEATURE_EAGER_LOADING")
        assert hasattr(settings.performance, "FEATURE_AUDIT_FLUSH_LIMIT")
        assert hasattr(settings.performance, "FEATURE_CIRCUIT_BREAKER")
        assert hasattr(settings.performance, "FEATURE_EVENT_RETRY_DLQ")
        assert hasattr(settings.performance, "FEATURE_ATOMIC_CACHE_INVALIDATION")
        assert hasattr(settings.performance, "FEATURE_WEBHOOK_CONNECTION_MGMT")
        assert hasattr(settings.performance, "FEATURE_SKIP_RLS_FOR_ADMIN")

    def test_settings_performance_flags_match_defaults(self) -> None:
        """Test settings.performance flags match default values."""
        settings = Settings()

        assert settings.performance.FEATURE_REMOVE_WASTED_CONNECTION is True
        assert settings.performance.FEATURE_STREAMING_UPLOADS is True
        assert settings.performance.FEATURE_CONTEXTVAR_OPTIMIZATION is True
        assert settings.performance.FEATURE_BILLING_DICT_OPTIMIZATION is True
        assert settings.performance.FEATURE_EAGER_LOADING is True
        assert settings.performance.FEATURE_AUDIT_FLUSH_LIMIT is True
        assert settings.performance.FEATURE_CIRCUIT_BREAKER is False
        assert settings.performance.FEATURE_EVENT_RETRY_DLQ is False
        assert settings.performance.FEATURE_ATOMIC_CACHE_INVALIDATION is False
        assert settings.performance.FEATURE_WEBHOOK_CONNECTION_MGMT is False
        assert settings.performance.FEATURE_SKIP_RLS_FOR_ADMIN is False
