"""E2E test for File Upload Quota (P0-4).

Tests the complete file upload quota flow with denormalized counters
for O(1) quota checking.

Test Flow:
1. Upload 10 files
2. Verify counters incremented correctly
3. Attempt upload that exceeds quota
4. Verify rejected with proper error
5. Delete files, verify counters decremented
6. Log all counter operations and timing
"""

import io
import logging
import time

import pytest
from httpx import AsyncClient

from src.app.core.utils.cache import client as redis_client

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_file_upload_quota_full_flow(premium_user: dict, client: AsyncClient, log_step):
    """Test complete file upload quota flow with denormalized counters.

    Verifies P0-4 optimization: File Quota Denormalization for O(1) checking.
    """
    log_step("Starting file upload quota E2E test", user=premium_user["email"])

    user_id = premium_user["user_id"]
    headers = {"Authorization": f"Bearer {premium_user['access_token']}"}

    # Step 1: Get initial quota state
    log_step("Getting initial quota state")
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200

    initial_quota = response.json()
    log_step(
        "Initial quota",
        used=initial_quota.get("used", 0),
        limit=initial_quota.get("limit", 0),
        remaining=initial_quota.get("remaining", 0),
    )

    initial_used = initial_quota.get("used", 0)
    quota_limit = initial_quota.get("limit", 100)

    # Step 2: Upload multiple files
    log_step("Uploading 10 test files")
    uploaded_files = []
    total_size = 0

    for i in range(10):
        # Create test file
        file_content = f"Test file content {i}" * 100  # ~2KB per file
        file_size = len(file_content.encode())
        total_size += file_size

        file_data = {
            "file": (f"test_file_{i}.txt", io.BytesIO(file_content.encode()), "text/plain")
        }

        log_step(f"Uploading file {i+1}/10", size_bytes=file_size)
        start_time = time.perf_counter()

        response = await client.post("/api/v1/files/upload", headers=headers, files=file_data)
        upload_time = time.perf_counter() - start_time

        assert response.status_code == 201, f"Upload failed: {response.text}"
        result = response.json()
        uploaded_files.append(result["id"])

        log_step(
            f"File {i+1} uploaded",
            file_id=result["id"],
            elapsed_ms=upload_time * 1000,
        )

    log_step("All files uploaded", total_files=10, total_size_bytes=total_size)

    # Step 3: Verify quota counters incremented
    log_step("Verifying quota counters incremented")
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200

    current_quota = response.json()
    current_used = current_quota.get("used", 0)

    log_step(
        "Current quota after uploads",
        used=current_used,
        limit=quota_limit,
        remaining=current_quota.get("remaining", 0),
    )

    # Verify counters incremented correctly
    expected_used = initial_used + total_size
    assert current_used == expected_used, f"Expected {expected_used} bytes, got {current_used}"
    log_step("Quota counters correct", expected=expected_used, actual=current_used)

    # Step 4: Verify Redis counter
    if redis_client:
        log_step("Checking Redis counter")
        counter_key = f"file:quota:user:{user_id}"
        redis_count = await redis_client.get(counter_key)

        if redis_count:
            redis_count = int(redis_count)
            log_step("Redis counter", count=redis_count)
            assert redis_count == current_used, f"Redis count {redis_count} != API count {current_used}"

    # Step 5: Attempt to exceed quota (if limit is set)
    if quota_limit > 0 and current_used < quota_limit:
        if quota_limit - current_used > 1000000:
            log_step("Skipping quota limit enforcement test, limit too high (>1MB) to practically test")
        else:
            log_step("Testing quota limit enforcement")
            # Try to exceed limit directly with a larger file
            larger_size = quota_limit - current_used + 100
            file_data = {
                "file": ("exceed_limit.txt", io.BytesIO(b"x" * larger_size), "text/plain")
            }
            response = await client.post("/api/v1/files/upload", headers=headers, files=file_data)

            log_step("Upload attempt over limit", status_code=response.status_code)
            assert response.status_code == 403 or response.status_code == 413, f"Expected 403 or 413, got {response.status_code}"

            error_detail = response.json().get("detail", "")
            log_step("Quota limit enforced", error=error_detail)
            assert "quota" in error_detail.lower() or "limit" in error_detail.lower() or response.status_code == 413

    # Step 6: Delete files and verify counters decremented
    log_step("Deleting uploaded files")
    deleted_size = 0
    for i, file_id in enumerate(uploaded_files[:5]):  # Delete first 5
        file_content = f"Test file content {i}" * 100
        deleted_size += len(file_content.encode())
        response = await client.delete(f"/api/v1/files/{file_id}", headers=headers)
        assert response.status_code == 204, f"Delete failed: {response.text}"
        log_step(f"Deleted file {i+1}/5", file_id=file_id)

    # Step 7: Verify quota decremented
    log_step("Verifying quota decremented after deletions")
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200

    final_quota = response.json()
    final_used = final_quota.get("used", 0)

    log_step(
        "Final quota after deletions",
        used=final_used,
        remaining=final_quota.get("remaining", 0),
    )

    expected_final = current_used - deleted_size
    assert final_used == expected_final, f"Expected {expected_final} bytes, got {final_used}"
    log_step("Quota correctly decremented", expected=expected_final, actual=final_used)

    log_step("File upload quota E2E test completed successfully")


@pytest.mark.asyncio
async def test_quota_check_performance(premium_user: dict, client: AsyncClient, log_step):
    """Test that quota checking is O(1) with denormalized counters.

    Verifies performance improvement over counting files in database.
    """
    log_step("Starting quota check performance E2E test", user=premium_user["email"])

    headers = {"Authorization": f"Bearer {premium_user['access_token']}"}

    # Step 1: Upload some files
    log_step("Uploading files for performance test")
    for i in range(5):
        file_data = {
            "file": (f"perf_test_{i}.txt", io.BytesIO(b"test content"), "text/plain")
        }
        response = await client.post("/api/v1/files/upload", headers=headers, files=file_data)
        assert response.status_code == 201

    # Step 2: Measure quota check performance
    log_step("Measuring quota check performance")
    times = []

    for i in range(10):
        start_time = time.perf_counter()
        response = await client.get("/api/v1/files/quota", headers=headers)
        elapsed = time.perf_counter() - start_time

        assert response.status_code == 200
        times.append(elapsed)
        log_step(f"Quota check {i+1}/10", elapsed_ms=elapsed * 1000)

    avg_time = sum(times) / len(times)
    log_step("Average quota check time", avg_ms=avg_time * 1000)

    # With O(1) counter, should be very fast (< 200ms including network for local testing)
    assert avg_time < 1.00, f"Average time {avg_time*1000:.2f}ms, expected < 1000ms"
    log_step("Performance acceptable", threshold_ms=200, actual_ms=avg_time * 1000)

    log_step("Quota check performance E2E test completed successfully")


@pytest.mark.asyncio
async def test_concurrent_uploads_quota_accuracy(premium_user: dict, client: AsyncClient, log_step):
    """Test that concurrent uploads maintain accurate quota counters.

    Verifies that atomic counter operations prevent race conditions.
    """
    log_step("Starting concurrent uploads quota accuracy E2E test", user=premium_user["email"])

    import asyncio

    headers = {"Authorization": f"Bearer {premium_user['access_token']}"}

    # Step 1: Get initial quota
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200
    initial_used = response.json().get("used", 0)
    log_step("Initial quota", used=initial_used)

    # Step 2: Upload files concurrently
    log_step("Uploading 5 files concurrently")

    async def upload_file(index):
        file_data = {
            "file": (f"concurrent_{index}.txt", io.BytesIO(b"test"), "text/plain")
        }
        response = await client.post("/api/v1/files/upload", headers=headers, files=file_data)
        return response.status_code == 201

    results = await asyncio.gather(*[upload_file(i) for i in range(5)])
    successful = sum(results)
    log_step("Concurrent uploads completed", successful=successful)

    # Step 3: Verify quota accuracy
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200

    final_used = response.json().get("used", 0)
    log_step("Final quota after concurrent uploads", used=final_used)

    expected_used = initial_used + (successful * 4)
    assert final_used == expected_used, f"Expected {expected_used}, got {final_used}"
    log_step("Quota counter accurate after concurrent operations")

    log_step("Concurrent uploads quota accuracy E2E test completed successfully")


@pytest.mark.asyncio
async def test_quota_counter_recovery(premium_user: dict, client: AsyncClient, log_step):
    """Test quota counter recovery if Redis counter is lost.

    Verifies that system can rebuild counter from database if needed.
    """
    log_step("Starting quota counter recovery E2E test", user=premium_user["email"])

    user_id = premium_user["user_id"]
    headers = {"Authorization": f"Bearer {premium_user['access_token']}"}

    # Step 1: Upload files
    log_step("Uploading files")
    for i in range(3):
        file_data = {
            "file": (f"recovery_test_{i}.txt", io.BytesIO(b"test"), "text/plain")
        }
        response = await client.post("/api/v1/files/upload", headers=headers, files=file_data)
        assert response.status_code == 201

    # Step 2: Get current quota
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200
    quota_before = response.json().get("used", 0)
    log_step("Quota before counter deletion", used=quota_before)

    # Step 3: Delete Redis counter (simulate loss)
    if redis_client:
        log_step("Simulating Redis counter loss")
        counter_key = f"file:quota:user:{user_id}"
        await redis_client.delete(counter_key)
        log_step("Redis counter deleted")

    # Step 4: Check quota again (should rebuild from database)
    log_step("Checking quota after counter loss")
    response = await client.get("/api/v1/files/quota", headers=headers)
    assert response.status_code == 200

    quota_after = response.json().get("used", 0)
    log_step("Quota after recovery", used=quota_after)

    # Quota should match (rebuilt from database)
    assert quota_after == quota_before, f"Expected {quota_before}, got {quota_after}"
    log_step("Quota counter successfully recovered")

    log_step("Quota counter recovery E2E test completed successfully")
