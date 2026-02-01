"""Pytest plugin for test isolation and cleanup.

This plugin ensures proper cleanup of FastAPI app state between tests,
preventing state pollution that causes failures in coverage runs.
"""

import pytest


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Clean up app dependency overrides after each test.

    This hook runs after every test to ensure app.dependency_overrides
    is cleared, preventing state pollution between tests.

    Critical for coverage runs where all test modules are loaded simultaneously.
    """
    yield  # Run the test teardown

    # Import app lazily to avoid module-level import issues
    try:
        from src.app.main import app

        # Clear all dependency overrides to prevent pollution
        if hasattr(app, "dependency_overrides"):
            app.dependency_overrides.clear()
    except Exception:
        # If app import fails, nothing to clean up
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "isolated: mark test as requiring complete isolation (clears all state)",
    )
