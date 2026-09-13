"""Billing unit-test fixtures.

The StripeGateway per-tenant API-key cache (K5) is module-level state; tests
that mock ``crud_stripe_settings.get`` with different rows for the same
tenant_id must not poison each other through it. Clear it around every test.
"""

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _clear_stripe_api_key_cache() -> Generator[None, None, None]:
    from src.app.clients.billing.stripe import _stripe_api_key_cache

    _stripe_api_key_cache.clear()
    yield
    _stripe_api_key_cache.clear()
