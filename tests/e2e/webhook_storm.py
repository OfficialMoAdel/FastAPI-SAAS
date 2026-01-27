import asyncio
import time
import httpx
import logging
from uuid import uuid4

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("webhook_storm")

BASE_URL = "http://localhost:8000/api/v1"
# Admin token required to create endpoints/trigger? Or use a test tenant.
# For E2E, we assume we have a valid token.
# This script expects an ACCESS_TOKEN env var or hardcoded for local testing.
ACCESS_TOKEN = "YOUR_ADMIN_TOKEN_HERE"

HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}


async def create_endpoints(client: httpx.AsyncClient, tenant_id: str, count: int):
    """Create N webhook endpoints for the tenant."""
    tasks = []
    for i in range(count):
        payload = {
            "url": f"https://example.com/webhook/{i}",
            "event_type": "storm.event",
            "secret": "test_secret",
            "is_active": True,
        }
        tasks.append(client.post(f"{BASE_URL}/webhooks/endpoints", json=payload, headers=HEADERS))

    logger.info(f"Creating {count} endpoints...")
    results = await asyncio.gather(*tasks)

    success = sum(1 for r in results if r.status_code == 201)
    logger.info(f"Created {success}/{count} endpoints.")
    return success == count


async def trigger_event(client: httpx.AsyncClient, tenant_id: str):
    """Trigger the event and measure time."""
    payload = {"event_type": "storm.event", "payload": {"data": "storm_test"}}

    start_time = time.perf_counter()
    response = await client.post(f"{BASE_URL}/webhooks/trigger", json=payload, headers=HEADERS)
    end_time = time.perf_counter()

    duration_ms = (end_time - start_time) * 1000
    logger.info(f"Trigger call took: {duration_ms:.2f}ms")

    if response.status_code == 200:
        data = response.json()
        delivery_count = len(data)
        logger.info(f"Response reported {delivery_count} deliveries initiated.")
        return delivery_count, duration_ms
    else:
        logger.error(f"Trigger failed: {response.text}")
        return 0, duration_ms


async def main():
    if ACCESS_TOKEN == "YOUR_ADMIN_TOKEN_HERE":
        logger.error("Please set ACCESS_TOKEN in the script before running.")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Setup Tenant (Optional, or use existing)
        # For simplicity, we assume we use the user's current tenant context
        # In a real E2E, we might create a fresh tenant.

        # 2. Create Endpoints
        # First, let's create 100 endpoints
        logger.info("Starting Webhook Storm Setup...")
        await create_endpoints(client, "current", 100)

        # 3. Trigger Storm
        logger.info("Triggering Storm Event...")
        count, duration = await trigger_event(client, "current")

        # 4. Verify
        if count == 100:
            logger.info("SUCCESS: 100 deliveries created.")
        else:
            logger.error(f"FAILURE: Expected 100, got {count}")

        if duration > 200:
            logger.warning(f"PERFORMANCE WARNING: Trigger took {duration:.2f}ms (Goal: <200ms)")
        else:
            logger.info("PERFORMANCE: Excellent.")


if __name__ == "__main__":
    asyncio.run(main())
