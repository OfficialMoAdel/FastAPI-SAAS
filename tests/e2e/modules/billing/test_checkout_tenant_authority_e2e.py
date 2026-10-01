"""E2E: checkout.session.completed resolves its tenant from the DB row.

Drives the real HTTP route (``POST /api/v1/billing/webhook``) against the
test database, doubling only the trust boundary (Stripe signature
verification) and the outbound subscription fetch, so plan ownership and
the resulting ``user_subscriptions`` row are resolved from real rows.

Two deliveries are exercised:

* honest metadata (what our own producer writes) — a subscription is minted;
* metadata naming a DIFFERENT tenant than the plan's real owner — the
  delivery is acked and NO subscription is written, because the plan row is
  the tenant authority.
"""

import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from src.app.modules.billing.models import SubscriptionPlan, UserSubscription
from src.app.modules.tenants.models import Tenant
from tests.conftest import patch_setting

WEBHOOK_URL = "/api/v1/billing/webhook"

STRIPE_SUB_DATA = {
    "current_period_start": 1600000000,
    "current_period_end": 1600000000 + 31536000,
    "status": "active",
    "cancel_at_period_end": False,
    "items": {"data": [{"price": {"recurring": {"interval": "year"}}}]},
}


class _RecordingLogger:
    """Captures the billing webhook package's log calls.

    ``structlog.testing.capture_logs`` is not used: other suites reconfigure
    the global processor chain and silently neuter it. Swapping the package's
    logger object intercepts the call site regardless of run order.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, args: tuple, kwargs: dict) -> None:
        self.records.append((level, str(event) % args if args else str(event), kwargs))

    def debug(self, event: str = "", *args, **kwargs) -> None:
        self._record("debug", event, args, kwargs)

    def info(self, event: str = "", *args, **kwargs) -> None:
        self._record("info", event, args, kwargs)

    def warning(self, event: str = "", *args, **kwargs) -> None:
        self._record("warning", event, args, kwargs)

    def error(self, event: str = "", *args, **kwargs) -> None:
        self._record("error", event, args, kwargs)

    def exception(self, event: str = "", *args, **kwargs) -> None:
        self._record("exception", event, args, kwargs)


@pytest.fixture
def webhook_env():
    """Neutralise Redis/Swagger config so the webhook route boots in tests."""
    mock_redis = AsyncMock()
    mock_redis.exists.return_value = 0
    mock_redis.get.return_value = None
    fake_pipeline = AsyncMock()
    fake_pipeline.__aenter__.return_value = fake_pipeline
    fake_pipeline.execute = AsyncMock(return_value=[False, False])
    mock_redis.pipeline = MagicMock(return_value=fake_pipeline)

    with (
        patch("src.app.core.setup.create_redis_queue_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.close_redis_cache_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.close_redis_queue_pool", new_callable=AsyncMock),
        patch("src.app.core.setup.create_redis_cache_pool", new_callable=AsyncMock),
        patch("src.app.core.utils.cache.client", mock_redis),
        patch_setting("STRIPE_WEBHOOK_SECRET", SecretStr("whsec_test")),
        patch_setting("STRIPE_SECRET_KEY", SecretStr("sk_test_dummy")),
    ):
        yield


class _Ctx:
    """Hands the shared test session to the handler's RLS contexts.

    SET LOCAL is unsupported on SQLite, so the tenant context is bound via
    the tenancy ContextVar instead of a Postgres GUC.
    """

    def __init__(self, session, tenant_id=None) -> None:
        self._session = session
        self._tenant_id = tenant_id

    async def __aenter__(self):
        if self._tenant_id is not None:
            from src.app.core.tenancy.context import set_current_tenant

            set_current_tenant(_StubTenant(self._tenant_id))
        return self._session

    async def __aexit__(self, *exc) -> None:
        if self._tenant_id is not None:
            from src.app.core.tenancy.context import clear_tenant_context

            clear_tenant_context()


class _StubTenant:
    def __init__(self, tenant_id) -> None:
        self.id = tenant_id
        self.slug = "e2e-checkout"
        self.name = "E2E Checkout"
        self.is_active = True


def _stripe_sub_double() -> MagicMock:
    sub = MagicMock()
    sub.get.side_effect = lambda key, default=None: STRIPE_SUB_DATA.get(key, default)
    sub.__getitem__.side_effect = lambda key: STRIPE_SUB_DATA[key]
    return sub


async def _post_checkout_delivery(client, db_session, session_ctx, *, metadata_tenant, sub_id, key_resolver):
    """Send one checkout.session.completed delivery through the HTTP route.

    ``key_resolver`` stands in for per-tenant Stripe key resolution: the
    forged-tenant case makes it RAISE, because no real tenant owns that key.
    A rejection therefore cannot come from the outbound call simply never
    happening — it must come from the handler refusing the payload itself.
    """
    event = {
        "id": f"evt_{uuid.uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": f"cs_{uuid.uuid4().hex}",
                "metadata": {
                    "tenant_id": str(metadata_tenant),
                    "user_id": str(session_ctx["user_id"]),
                    "plan_id": str(session_ctx["plan_id"]),
                },
                "subscription": sub_id,
            }
        },
    }
    recorder = _RecordingLogger()
    with ExitStack() as stack:
        stack.enter_context(patch("src.app.modules.billing.router.verify_webhook_signature", return_value=event))
        stack.enter_context(
            patch("src.app.modules.billing.webhook.logger", recorder),
        )
        stack.enter_context(
            patch("src.app.modules.billing.webhook.job_bypass_context", lambda *a, **k: _Ctx(db_session)),
        )
        stack.enter_context(
            patch(
                "src.app.modules.billing.webhook.job_tenant_context",
                lambda tenant_id, *a, **k: _Ctx(db_session, tenant_id),
            ),
        )
        stack.enter_context(
            patch("stripe.Subscription.retrieve", return_value=_stripe_sub_double()),
        )
        stack.enter_context(
            patch(
                "src.app.modules.billing.webhook.handlers.checkout.resolve_tenant_stripe_api_key",
                new=AsyncMock(side_effect=key_resolver),
            ),
        )
        response = client.post(WEBHOOK_URL, json=event, headers={"Stripe-Signature": "dummy_sig"})
    return response, recorder


@pytest.mark.asyncio
async def test_honest_metadata_tenant_mints_subscription(webhook_env, client, db_session, tenant, create_test_user):
    """The producer's real payload still mints, for the DB-plan tenant."""
    user = await create_test_user(email="checkout_honest@example.com")

    await db_session.execute(delete(SubscriptionPlan).where(SubscriptionPlan.id == 5001))
    plan = SubscriptionPlan(
        tenant_id=tenant.id,
        name="Checkout Honest",
        price_monthly_cents=1000,
        price_yearly_cents=10000,
    )
    plan.id = 5001
    db_session.add(plan)
    await db_session.commit()

    ctx = {"user_id": user.id, "plan_id": plan.id}

    async def _platform_key(db, tenant_id):
        return "sk_test_platform"

    response, _recorder = await _post_checkout_delivery(
        client, db_session, ctx, metadata_tenant=tenant.id, sub_id="sub_e2e_honest", key_resolver=_platform_key
    )

    assert response.status_code == status.HTTP_200_OK

    result = await db_session.execute(
        select(UserSubscription).where(UserSubscription.stripe_subscription_id == "sub_e2e_honest")
    )
    subscription = result.scalar_one_or_none()
    assert subscription is not None
    assert subscription.tenant_id == tenant.id
    assert subscription.user_id == user.id
    assert subscription.status == "active"


@pytest.mark.asyncio
async def test_bogus_metadata_tenant_writes_nothing(webhook_env, client, db_session, tenant, create_test_user):
    """Metadata naming another tenant must not create a row anywhere."""
    user = await create_test_user(email="checkout_bogus@example.com")
    attacker = Tenant(
        name="Attacker Co",
        slug=f"attacker-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db_session.add(attacker)
    await db_session.commit()
    await db_session.refresh(attacker)

    await db_session.execute(delete(SubscriptionPlan).where(SubscriptionPlan.id == 5002))
    plan = SubscriptionPlan(
        tenant_id=tenant.id,
        name="Checkout Bogus",
        price_monthly_cents=1000,
        price_yearly_cents=10000,
    )
    plan.id = 5002
    db_session.add(plan)
    await db_session.commit()

    ctx = {"user_id": user.id, "plan_id": plan.id}

    async def _no_key_for_the_forged_tenant(db, tenant_id):
        raise AssertionError(f"handler resolved a Stripe key for tenant {tenant_id} on a forged session")

    response, recorder = await _post_checkout_delivery(
        client,
        db_session,
        ctx,
        metadata_tenant=attacker.id,
        sub_id="sub_e2e_bogus",
        key_resolver=_no_key_for_the_forged_tenant,
    )

    # Acked (deterministic rejection — a retry can never heal a forged claim).
    assert response.status_code == status.HTTP_200_OK

    count = await db_session.execute(
        select(func.count())
        .select_from(UserSubscription)
        .where(UserSubscription.stripe_subscription_id == "sub_e2e_bogus")
    )
    assert count.scalar_one() == 0

    mismatches = [r for r in recorder.records if "disagrees" in r[1]]
    assert mismatches, f"forged tenant refusal not logged: {recorder.records}"
    _, _, kwargs = mismatches[0]
    assert kwargs["claimed_tenant_id"] == str(attacker.id)
    assert kwargs["db_tenant_id"] == str(tenant.id)
