"""
Circuit Breaker Registry Usage Examples.

This script demonstrates how to use the centralized Circuit Breaker Registry
for managing, monitoring, and controlling circuit breakers across your application.
"""

import asyncio
import logging

from src.app.core.resilience import CircuitState, ServiceType, get_registry
from src.app.core.utils.circuit_breaker import CircuitBreaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Example 1: Creating and Registering Circuit Breakers
# -----------------------------------------------------------------------------
async def register_breakers():
    """Demonstrate different ways to register circuit breakers."""
    print("\n[1] Registering Circuit Breakers...")
    registry = get_registry()

    # Method A: Create via registry
    # Best for runtime creation or configuration-driven setup
    registry.create(
        name="payment_gateway",
        service_type=ServiceType.EXTERNAL_API,
        description="Stripe Payment Gateway",
        failure_threshold=3,
        recovery_timeout=30.0,
    )
    print(" - Registered 'payment_gateway'")

    # Method B: Register existing instance
    # Useful when you have custom configured breakers
    email_breaker = CircuitBreaker(name="email_service", failure_threshold=5)
    registry.register(
        name="email_service",
        breaker=email_breaker,
        service_type=ServiceType.EMAIL,
        description="SendGrid Email Service",
        tags=["critical", "notification"],
    )
    print(" - Registered 'email_service'")


# -----------------------------------------------------------------------------
# Example 2: Service Integration
# -----------------------------------------------------------------------------
class WebhookService:
    def __init__(self):
        # Explicitly create/retrieve the circuit breaker for this service
        registry = get_registry()

        # Check if it already exists (e.g. from previous run or other init)
        self.circuit = registry.get("webhook_delivery")

        if not self.circuit:
            self.circuit = registry.create(
                name="webhook_delivery",
                service_type=ServiceType.WEBHOOK,
                description="Webhook Delivery Service",
                tags=["background", "retryable"],
            )
            print(" - Registered 'webhook_delivery' (Service Integration)")
        else:
            print(" - Retrieved existing 'webhook_delivery'")

    async def send_webhook(self, url: str, payload: dict):
        # Use the circuit breaker logic
        if self.circuit.state == CircuitState.OPEN:
            print("Circuit OPEN: Skipping webhook")
            return

        try:
            # Simulate work
            print(f"Sending webhook to {url}...")
            # Simulate random failure
            # import random
            # if random.random() < 0.2:
            #     raise Exception("Random network glitch")

            await asyncio.sleep(0.1)
            await self.circuit.record_success()
        except (TimeoutError, OSError) as e:
            print(f"Webhook failed: {e}")
            await self.circuit.record_failure(e)


# -----------------------------------------------------------------------------
# Example 3: Filtering and Monitoring
# -----------------------------------------------------------------------------
async def monitor_registry():
    """Demonstrate how to query the registry for monitoring."""
    print("\n[3] Monitoring Registry...")
    registry = get_registry()

    # Get health summary (perfect for dashboards)
    summary = registry.get_health_summary()
    print(f"Health Summary: {summary['health_percentage']}% Healthy")
    print(f" - Total: {summary['total']}")
    print(f" - Open: {summary['open']}")
    print(f" - Closed: {summary['closed']}")

    # Filter by Service Type
    external_apis = registry.get_by_service_type(ServiceType.EXTERNAL_API)
    print(f"\nExternal APIs ({len(external_apis)}):")
    for breaker in external_apis:
        print(f" - {breaker.name}: {breaker.state.value}")

    # Filter by Tag
    critical_services = registry.get_by_tag("critical")
    print(f"\nCritical Services ({len(critical_services)}):")
    for name, spec in critical_services.items():
        print(f" - {name} ({spec.description})")


# -----------------------------------------------------------------------------
# Example 4: Admin Control Operations
# -----------------------------------------------------------------------------
async def admin_operations():
    """Demonstrate administrative operations like resetting circuits."""
    print("\n[4] Admin Operations...")
    registry = get_registry()
    breaker = registry.get("payment_gateway")

    # Simulate failures to open the circuit
    print("Simulating failures for 'payment_gateway'...")
    for _ in range(5):
        await breaker.record_failure(Exception("Connection timeout"))

    print(f"State after failures: {breaker.state.value}")

    # Manual Reset (e.g., triggered via Admin API)
    print("Resetting 'payment_gateway'...")
    await registry.reset("payment_gateway")
    print(f"State after reset: {breaker.state.value}")

    # Reset All (Emergency Override)
    print("Resetting ALL breakers...")
    count = await registry.reset_all()
    print(f"Reset {count} breakers.")


async def main():
    await register_breakers()

    webhook_service = WebhookService()
    await webhook_service.send_webhook("http://example.com", {})

    await admin_operations()
    await monitor_registry()


if __name__ == "__main__":
    asyncio.run(main())
