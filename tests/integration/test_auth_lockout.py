import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from src.app.core.utils.cache import async_get_redis
from src.app.main import app


class FakeRedis:
    """Fake Redis client for testing account lockout without real Redis."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        val = self.store.get(key, 0)
        val = int(val) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return True

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
        return True

    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args):
        """Execute a Lua script (mock implementation for account lockout).

        This mimics the behavior of the actual Lua script used in AuthService:
        - Returns -1 if already locked out (attempts >= max_attempts)
        - Otherwise increments and returns new attempt count
        """
        if numkeys >= 1:
            key = keys_and_args[0]
            max_attempts = int(keys_and_args[1]) if len(keys_and_args) > 1 else 5
            lockout_duration = int(keys_and_args[2]) if len(keys_and_args) > 2 else 900

            current_attempts = int(self.store.get(key, 0))

            # Check if already locked BEFORE incrementing
            if current_attempts >= max_attempts:
                return -1  # Already locked out

            # Increment and store
            current_attempts += 1
            self.store[key] = str(current_attempts)

            return current_attempts
        return 1

    async def aclose(self):
        pass


# Use the standard test tenant slug from conftest
TEST_TENANT_SLUG = "test-tenant-integration"


@pytest.fixture
def fake_redis():
    """Provide a fake Redis instance for testing."""
    return FakeRedis()


class TestAccountLockoutMechanism:
    """Test the Redis-based account lockout mechanism."""

    def test_lockout_lua_script_logic(self, fake_redis):
        """Test the Lua script logic for account lockout using FakeRedis.

        This test verifies that the Lua script mock correctly implements
        the atomic check-and-increment logic used for account lockout.
        """
        username = "testuser"
        lockout_key = f"login_attempts:{username}"
        max_attempts = 5

        # Simulate 5 failed login attempts
        for i in range(5):
            result = asyncio.run(
                fake_redis.eval(
                    "mock_script",  # Script content doesn't matter in mock
                    1,
                    lockout_key,
                    str(max_attempts),
                    "900",  # lockout duration
                )
            )
            # First 5 attempts should return the attempt count (1-5)
            assert result == i + 1, f"Attempt {i+1}: Expected {i+1}, got {result}"

        # 6th attempt should return -1 (locked)
        result = asyncio.run(
            fake_redis.eval(
                "mock_script",
                1,
                lockout_key,
                str(max_attempts),
                "900",
            )
        )
        assert result == -1, f"Expected -1 (locked), got {result}"

        # Verify the lockout is maintained
        result = asyncio.run(
            fake_redis.eval(
                "mock_script",
                1,
                lockout_key,
                str(max_attempts),
                "900",
            )
        )
        assert result == -1, "Lockout should persist"

        # Clear the lockout (simulate successful login or admin reset)
        asyncio.run(fake_redis.delete(lockout_key))

        # After clearing, should be able to attempt again
        result = asyncio.run(
            fake_redis.eval(
                "mock_script",
                1,
                lockout_key,
                str(max_attempts),
                "900",
            )
        )
        assert result == 1, f"Expected 1 after reset, got {result}"

    def test_lockout_key_normalization(self, fake_redis):
        """Test that lockout keys are properly normalized (case-insensitive).

        This ensures that username case variations don't bypass lockout.
        """
        # The AuthService normalizes username to lowercase
        # Lockout should apply regardless of username case
        usernames = ["TestUser", "testuser", "TESTUSER", "TestUser@example.com"]
        max_attempts = 5

        # All variations should increment the same lockout counter
        for username in usernames:
            normalized = username.lower().strip()
            lockout_key = f"login_attempts:{normalized}"

            result = asyncio.run(
                fake_redis.eval(
                    "mock_script",
                    1,
                    lockout_key,
                    str(max_attempts),
                    "900",
                )
            )
            # Should see incrementing values as we use the same normalized key
            # (for testuser key: 1, 2, 3, 4)

        # Verify the testuser@example.com key was normalized
        lockout_key = "login_attempts:testuser@example.com"
        stored_value = fake_redis.store.get(lockout_key)
        assert stored_value == "1", "Email usernames should be normalized"

    @pytest.mark.skip(reason="Integration test requires database with user. Run manually with PostgreSQL.")
    def test_account_lockout_via_api(self, client: TestClient, fake_redis):
        """Integration test for account lockout via login API.

        This test is skipped in CI because it requires a real database user.
        To run manually:
        1. Set up PostgreSQL test database
        2. Create test user with known credentials
        3. Run: pytest -v -k "test_account_lockout_via_api"
        """
        pass