"""
Job Monitoring Service Usage Example

This script demonstrates how to use the JobMonitoringService to track job execution,
retrieve metrics, and inspect the Dead Letter Queue (DLQ).
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.app.core.job_monitoring import JobMonitoringService
from src.app.core.setup import close_redis_cache_pool, create_redis_cache_pool


async def main():
    print("Starting Job Monitoring Example")

    # Initialize Redis (required for the service)
    await create_redis_cache_pool()

    try:
        service = JobMonitoringService()
        function_name = "example_email_task"

        # 1. Simulate Job Success
        job_id_success = "job_success_001"
        start_time = datetime.now(UTC).timestamp()

        print(f"\n[1] Starting job {job_id_success} ({function_name})...")
        await service.record_job_start(job_id_success, function_name)

        # Simulate work
        await asyncio.sleep(0.1)

        print(f"[2] Job {job_id_success} completed successfully.")
        await service.record_job_success(job_id_success, function_name, start_time)

        # 2. Simulate Job Failure
        job_id_fail = "job_fail_002"
        datetime.now(UTC).timestamp()

        print(f"\n[3] Starting job {job_id_fail} ({function_name})...")
        await service.record_job_start(job_id_fail, function_name)

        try:
            # Simulate error
            raise ValueError("Connection timeout")
        except ValueError as e:
            print(f"[4] Job {job_id_fail} failed: {e}")
            await service.record_job_failure(job_id_fail, function_name, str(e))

        # 3. Retrieve Metrics
        print("\n[5] Retrieving Metrics...")
        all_metrics = await service.get_job_metrics()
        metrics = all_metrics.get(function_name, {})
        print(f"Metrics for '{function_name}':")
        print(f"   - Total: {metrics.get('total', 0)}")
        print(f"   - Success: {metrics.get('success', 0)}")
        print(f"   - Failed: {metrics.get('failed', 0)}")

        # 4. Inspect DLQ
        print("\n[6] Inspecting Dead Letter Queue (DLQ)...")
        dlq = await service.get_dead_letter_queue()
        if dlq:
            print(f"Found {len(dlq)} failed jobs in DLQ:")
            for item in dlq:
                print(f"   - Job ID: {item['job_id']}")
                print(f"     Function: {item['function']}")
                print(f"     Error: {item['error']}")
                print(f"     Time: {item['timestamp']}")
        else:
            print("No failed jobs in DLQ.")

        print("\nExample completed.")

    finally:
        await close_redis_cache_pool()


if __name__ == "__main__":
    asyncio.run(main())
