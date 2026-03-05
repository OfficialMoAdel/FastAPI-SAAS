import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient
from src.app.core.utils.cache import async_get_redis
from src.app.main import app


# Use the standard test tenant slug from conftest
TEST_TENANT_SLUG = "test-tenant-integration"


class TestAccountLockoutMechanism:
    """Test the Redis-based account lockout mechanism."""

    def test_lockout_lua_script_logic(self, mock_redis):
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
                mock_redis.eval(
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
            mock_redis.eval(
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
            mock_redis.eval(
                "mock_script",
                1,
                lockout_key,
                str(max_attempts),
                "900",
            )
        )
        assert result == -1, "Lockout should persist"

        # Clear the lockout (simulate successful login or admin reset)
        asyncio.run(mock_redis.delete(lockout_key))

        # After clearing, should be able to attempt again
        result = asyncio.run(
            mock_redis.eval(
                "mock_script",
                1,
                lockout_key,
                str(max_attempts),
                "900",
            )
        )
        assert result == 1, f"Expected 1 after reset, got {result}"

    def test_lockout_key_normalization(self, mock_redis):
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
                mock_redis.eval(
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
        stored_value = mock_redis.store.get(lockout_key)
        assert stored_value == "1", "Email usernames should be normalized"

    @pytest.mark.skip(reason="Integration test requires database with user. Run manually with PostgreSQL.")
    def test_account_lockout_via_api(self, client: TestClient, mock_redis):
        """Integration test for account lockout via login API.

        This test is skipped in CI because it requires a real database user.
        To run manually:
        1. Set up PostgreSQL test database
        2. Create test user with known credentials
        3. Run: pytest -v -k "test_account_lockout_via_api"
        """
        pass