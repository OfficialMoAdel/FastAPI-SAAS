"""Conftest for middleware unit tests.

This file overrides the root conftest to prevent database setup
for standalone middleware unit tests.
"""

import pytest


@pytest.fixture(scope="session")
def setup_db():
    """Override root setup_db to skip database creation."""
    pass


@pytest.fixture(scope="session")
def setup_metadata():
    """Override root setup_metadata to skip metadata setup."""
    pass
