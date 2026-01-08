import pytest
from src.app.modules.billing.webhook import verify_webhook_signature
from src.app.core.config import settings


@pytest.mark.skipif(not settings.STRIPE_SECRET_KEY, reason="Stripe keys not configured")
def test_webhook_contract():
    """
    Contract test for Stripe webhooks.
    Requires stripe-mock or real keys.
    """
    # Placeholder for contract test
    pass
