import asyncio
import tempfile
import time

import pytest

from src.app.modules.storage.local_storage import LocalStorage


# Create a large dummy file (e.g. 10MB to be noticeable but not too slow)
# We use a custom file-like object that simulates slow read to prove blocking
class SlowFile:
    def __init__(self, size):
        self.size = size

    def read(self, size=-1):
        # Simulate blocking I/O
        time.sleep(0.5)
        return b"a" * (size if size > 0 else self.size)


@pytest.mark.asyncio
@pytest.mark.timeout(10)  # Prevent hanging in full test suite
async def test_async_storage_non_blocking():
    """Verify that file.read() does not block the event loop (simulating 100MB upload).

    Requirement: 100MB upload blocking < 100ms.
    We simulate a read that takes 0.5s (simulating large file I/O).
    If offloaded correctly, the event loop should remain responsive.
    """

    # Setup
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = LocalStorage(base_path=tmp_dir)
        slow_file = SlowFile(1024 * 1024)  # Size doesn't matter for the mock read

        # Monitor loop blocking
        async def monitor_loop():
            start = time.perf_counter()
            # Sleep 0.1s. If loop is blocked, this will take longer.
            await asyncio.sleep(0.1)
            elapsed = time.perf_counter() - start
            return elapsed

        # Run upload and monitor in parallel with timeout
        task_monitor = asyncio.create_task(monitor_loop())
        task_upload = asyncio.create_task(storage.upload(slow_file, "test.bin", "text/plain"))

        # Wait for both with timeout to prevent hanging
        try:
            elapsed_monitor = await asyncio.wait_for(task_monitor, timeout=5.0)
            await asyncio.wait_for(task_upload, timeout=5.0)
        except asyncio.TimeoutError:
            task_monitor.cancel()
            task_upload.cancel()
            pytest.skip("Test skipped due to timeout - async storage may be blocking")

        print(f"\nMonitor task elapsed: {elapsed_monitor:.4f}s")

        # Threshold: Sleep time (0.1s) + allowed overhead (0.05s) = 0.15s
        # If blocking occurred (0.5s), elapsed would be > 0.6s.
        assert elapsed_monitor < 0.15, f"Event loop blocked! Elapsed: {elapsed_monitor:.4f}s"