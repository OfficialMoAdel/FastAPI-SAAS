"""Feature Flag Testing Fixtures.

Provides reusable pytest fixtures for testing feature flag behavior across
all performance optimizations. Each optimization has a feature flag that must
be tested in both ON and OFF states.

Examples:
    @pytest.mark.usefixtures("all_flags_enabled")
    async def test_optimization_with_flags():
        # Test with all optimization flags enabled
        pass

    @pytest.mark.usefixtures("all_flags_disabled")
    async def test_baseline_behavior():
        # Test with all flags disabled (baseline)
        pass

    def test_specific_flag(with_flag):
        with with_flag("FEATURE_STREAMING_UPLOADS", True):
            # Test with specific flag enabled
            pass
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from src.app.core.config import settings


@contextmanager
def _patch_flag(flag_name: str, value: bool):
    """Context manager to temporarily patch a feature flag."""
    original_value = getattr(settings, flag_name, None)
    patcher = patch.object(settings, flag_name, value)
    patcher.start()
    try:
        yield
    finally:
        patcher.stop()
        if original_value is not None:
            setattr(settings, flag_name, original_value)


@pytest.fixture
def with_flag():
    """Fixture factory to enable/disable specific feature flags.

    Returns a context manager that patches a single flag.

    Args:
        flag_name: Name of the feature flag to patch
        value: Boolean value to set (True/False)

    Example:
        def test_something(with_flag):
            with with_flag("FEATURE_STREAMING_UPLOADS", True):
                # Test with flag enabled
                pass

            with with_flag("FEATURE_STREAMING_UPLOADS", False):
                # Test with flag disabled
                pass
    """

    def _with_flag(flag_name: str, value: bool):
        return _patch_flag(flag_name, value)

    return _with_flag


@pytest.fixture
def all_flags_enabled():
    """Enable all performance optimization feature flags.

    Use this fixture when testing optimizations in their enabled state.
    This is the "after optimization" state.

    Example:
        @pytest.mark.usefixtures("all_flags_enabled")
        async def test_optimized_behavior():
            # All flags are enabled
            pass
    """
    flags = [
        "FEATURE_REMOVE_WASTED_CONNECTION",
        "FEATURE_STREAMING_UPLOADS",
        "FEATURE_CONTEXTVAR_OPTIMIZATION",
        "FEATURE_AUDIT_FLUSH_LIMIT",
    ]

    patches = []
    for flag in flags:
        if hasattr(settings, flag):
            patcher = patch.object(settings, flag, True)
            patcher.start()
            patches.append(patcher)

    yield

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def all_flags_disabled():
    """Disable all performance optimization feature flags.

    Use this fixture when testing baseline behavior without optimizations.
    This is the "before optimization" state.

    Example:
        @pytest.mark.usefixtures("all_flags_disabled")
        async def test_baseline_behavior():
            # All flags are disabled (baseline)
            pass
    """
    flags = [
        "FEATURE_REMOVE_WASTED_CONNECTION",
        "FEATURE_STREAMING_UPLOADS",
        "FEATURE_CONTEXTVAR_OPTIMIZATION",
        "FEATURE_AUDIT_FLUSH_LIMIT",
    ]

    patches = []
    for flag in flags:
        if hasattr(settings, flag):
            patcher = patch.object(settings, flag, False)
            patcher.start()
            patches.append(patcher)

    yield

    for patcher in patches:
        patcher.stop()


@pytest.fixture
def snapshot_flags():
    """Snapshot and restore all feature flags.

    Use this fixture when tests need to modify flags but want to restore
    the original state after completion.

    Example:
        def test_modify_flags(snapshot_flags):
            original = snapshot_flags()
            # Modify flags
            with _patch_flag("FEATURE_X", True):
                pass
            # Flags automatically restored
    """
    flags = [
        "FEATURE_REMOVE_WASTED_CONNECTION",
        "FEATURE_STREAMING_UPLOADS",
        "FEATURE_CONTEXTVAR_OPTIMIZATION",
        "FEATURE_AUDIT_FLUSH_LIMIT",
    ]

    snapshot = {flag: getattr(settings, flag, None) for flag in flags if hasattr(settings, flag)}

    yield snapshot

    for flag, value in snapshot.items():
        if value is not None:
            setattr(settings, flag, value)


@pytest.fixture
def flag_state_logger(caplog):
    """Log feature flag state in test output.

    Use this fixture to capture and log the state of all feature flags
    during test execution. This helps with debugging and test documentation.

    Example:
        def test_with_logging(flag_state_logger):
            flag_state_logger()
            # Test code here
    """

    def _log_flags():
        flags = [
            "FEATURE_REMOVE_WASTED_CONNECTION",
            "FEATURE_STREAMING_UPLOADS",
            "FEATURE_CONTEXTVAR_OPTIMIZATION",
            "FEATURE_AUDIT_FLUSH_LIMIT",
        ]

        flag_states = {}
        for flag in flags:
            if hasattr(settings, flag):
                flag_states[flag] = getattr(settings, flag)

        pytest.logger.info(f"Feature Flags State: {flag_states}")
        return flag_states

    return _log_flags


@pytest.fixture
def parametrize_flags():
    """Parametrize tests across flag combinations.

    Returns a list of flag combinations for use with pytest.mark.parametrize.

    Example:
        @pytest.mark.parametrize("flags", parametrize_flags())
        def test_with_flags(flags):
            for flag, value in flags.items():
                with _patch_flag(flag, value):
                    pass
    """
    combinations = [
        {
            "name": "all_enabled",
            "flags": dict.fromkeys(
                [
                    "FEATURE_REMOVE_WASTED_CONNECTION",
                    "FEATURE_STREAMING_UPLOADS",
                    "FEATURE_CONTEXTVAR_OPTIMIZATION",
                    "FEATURE_AUDIT_FLUSH_LIMIT",
                ],
                True,
            ),  # noqa: E501
        },
        {
            "name": "all_disabled",
            "flags": dict.fromkeys(
                [
                    "FEATURE_REMOVE_WASTED_CONNECTION",
                    "FEATURE_STREAMING_UPLOADS",
                    "FEATURE_CONTEXTVAR_OPTIMIZATION",
                    "FEATURE_AUDIT_FLUSH_LIMIT",
                ],
                False,
            ),  # noqa: E501
        },
        {
            "name": "streaming_only",
            "flags": {
                "FEATURE_STREAMING_UPLOADS": True,
                "FEATURE_REMOVE_WASTED_CONNECTION": False,
                "FEATURE_CONTEXTVAR_OPTIMIZATION": False,
                "FEATURE_AUDIT_FLUSH_LIMIT": False,
            },
        },
        {
            "name": "contextvar_only",
            "flags": {
                "FEATURE_STREAMING_UPLOADS": False,
                "FEATURE_REMOVE_WASTED_CONNECTION": False,
                "FEATURE_CONTEXTVAR_OPTIMIZATION": True,
                "FEATURE_AUDIT_FLUSH_LIMIT": False,
            },
        },
    ]

    return [(combo["name"], combo["flags"]) for combo in combinations]
