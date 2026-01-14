from unittest.mock import AsyncMock, patch

import pytest

from src.app.core.utils.cache import get_with_lock


@pytest.mark.asyncio
async def test_cache_lock_backoff_logic():
    """Verify that get_with_lock uses exponential backoff when waiting."""

    key = "test:lock:key"
    ttl = 60
    lock_timeout = 10

    # Mock Redis
    mock_redis = AsyncMock()

    # 1. Initial check: Cache miss (returns None)
    # 2. Lock acquire: Fails (returns False) -> enters wait loop
    # 3. Wait loop 1: Cache check returns None
    # 4. Wait loop 2: Cache check returns None
    # 5. Wait loop 3: Cache check returns Valid Data
    mock_redis.get.side_effect = [
        None,  # Initial check
        None,  # Check inside wait loop 1
        None,  # Check inside wait loop 2
        b'{"data": "success"}',  # Check inside wait loop 3
    ]

    mock_redis.set.return_value = False  # Lock acquisition failed

    fetch_func = AsyncMock()

    with patch("src.app.core.utils.cache.client", mock_redis):
        # Patch asyncio.sleep to track delays and avoid actual waiting
        with patch("src.app.core.utils.cache.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Patch random.uniform to return 0 (no jitter) for deterministic assertions
            with patch("src.app.core.utils.cache.random.uniform", return_value=0.0):
                result = await get_with_lock(key, fetch_func, ttl, lock_timeout)

                assert result == {"data": "success"}

                # Verify sleep calls
                # Expected delays with base=0.05:
                # 1. 0.05
                # 2. 0.10
                # 3. 0.20

                # Currently (Baseline), it sleeps 0.1 fixed.
                # If this test fails with current code, it confirms behavior.
                # If we want to assert target behavior:

                # Check call args
                sleep_times = [call.args[0] for call in mock_sleep.call_args_list]
                print(f"Sleep times: {sleep_times}")

                # Verify exponential backoff: 0.05 -> 0.1 -> 0.2
                # Note: random.uniform is mocked to 0.0, so exact values expected
                assert len(sleep_times) == 3
                assert sleep_times[0] == 0.05
                assert sleep_times[1] == 0.1
                assert sleep_times[2] == 0.2
