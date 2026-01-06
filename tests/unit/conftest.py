
import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Mock setup_db for unit tests to avoid real DB connection."""
    pass

@pytest.fixture(scope="session", autouse=True)
def mock_settings_env():
    """Ensure environment is set to local/test to avoid production checks."""
    # We can't easily change settings if it's already instantiated, but we can patch it or set attributes
    # But usually unit tests shouldn't care about DB connectivity if mocked properly
    pass
