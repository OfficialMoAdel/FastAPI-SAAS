"""Checkout webhook: the tenant comes from the DB plan row, not from metadata.

B6 tenant authority. Every other billing webhook handler re-resolves the
tenant from the DB row it matched (webhook.locks.find_subscription_by_stripe_id);
checkout.session.completed used to take ``session.metadata.tenant_id`` as
the tenant for the Stripe key, the tenant context and the written row, and
merely *compare* it against DB facts afterwards. Any disagreement therefore
rejected the event, but the deciding value was the attacker-controllable
string.

These tests pin the DB row as the authority: a claim that names a foreign
tenant is refused (and the refusal names both ids), a session with no claim
at all is decided purely by the row, and a user outside the row's owning
tenant is refused.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.modules.billing.webhook.handlers.checkout import (
    handle_checkout_session_completed,
)

HANDLER = "src.app.modules.billing.webhook.handlers.checkout"
WEBHOOK_PKG = "src.app.modules.billing.webhook"

STRIPE_SUB = {
    "status": "active",
    "current_period_start": 1600000000,
    "current_period_end": 1602592000,
    "cancel_at_period_end": False,
    "items": {"data": [{"price": {"recurring": {"interval": "month"}}}]},
}


class _RecordingLogger:
    """Logger-shaped recorder for the structlog calls the handler makes.

    ``structlog.testing.capture_logs`` is not used: other suites mutate the
    global processor chain, which silently turns the capture into a no-op
    depending on run order. Swapping the package's logger object intercepts
    the call site itself and cannot be perturbed by that.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, args: tuple, kwargs: dict) -> None:
        self.records.append((level, str(event) % args if args else str(event), kwargs))

    # *args: the handler mixes structlog kwargs with printf-style %s calls.
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

    def loud(self) -> list[tuple[str, str, dict]]:
        """Warning/error records — the operator-visible surface."""
        return [r for r in self.records if r[0] in ("warning", "error")]


class _CM:
    """Async context manager double over a single mocked session."""

    def __init__(self, db) -> None:
        self._db = db

    async def __aenter__(self) -> AsyncMock:
        return self._db

    async def __aexit__(self, *exc) -> None:
        return None


class _CheckoutHarness:
    """Wires the handler's collaborators to doubles and runs the handler."""

    def __init__(self, *, plan_tenant, user_tenant, stripe_sub: dict | None = None) -> None:
        self.db = AsyncMock()
        self.stripe_sub = STRIPE_SUB if stripe_sub is None else stripe_sub
        self.logger = _RecordingLogger()

        self.plan_service = MagicMock()
        self.plan_service.return_value.get_plan_tenant_id = AsyncMock(return_value=plan_tenant)
        self.get_user_tenant_id = AsyncMock(return_value=user_tenant)
        self.resolve_key = AsyncMock(return_value="sk_test_resolved")
        self.fetch_subscription = AsyncMock(return_value=self.stripe_sub)
        self.crud = MagicMock()
        self.crud.exists = AsyncMock(return_value=False)
        self.crud.create = AsyncMock()
        self.crud.update = AsyncMock()

    async def run(self, event: dict) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch(f"{HANDLER}.SubscriptionPlanService", self.plan_service))
            stack.enter_context(patch(f"{HANDLER}.get_user_tenant_id", self.get_user_tenant_id))
            stack.enter_context(patch(f"{HANDLER}.resolve_tenant_stripe_api_key", self.resolve_key))
            stack.enter_context(patch(f"{HANDLER}.fetch_stripe_subscription", self.fetch_subscription))
            stack.enter_context(patch(f"{WEBHOOK_PKG}.crud_user_subscriptions", self.crud))
            stack.enter_context(patch(f"{WEBHOOK_PKG}.job_bypass_context", lambda *a, **k: _CM(self.db)))
            stack.enter_context(patch(f"{WEBHOOK_PKG}.job_tenant_context", lambda *a, **k: _CM(self.db)))
            stack.enter_context(patch(f"{WEBHOOK_PKG}.logger", self.logger))
            await handle_checkout_session_completed(event)

    @property
    def created(self):
        return self.crud.create.call_args[1]["object"]


def _event(*, metadata_tenant: str | None, user_id: int = 7, plan_id: int = 3) -> dict:
    metadata: dict[str, str] = {"user_id": str(user_id), "plan_id": str(plan_id)}
    if metadata_tenant is not None:
        metadata["tenant_id"] = metadata_tenant
    return {
        "id": f"evt_{uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "metadata": metadata,
                "subscription": "sub_test_1",
            }
        },
    }


@pytest.mark.asyncio
async def test_forged_metadata_tenant_is_refused() -> None:
    """A session claiming tenant B must never mint a row for tenant B."""
    real_tenant = uuid4()
    attacker_tenant = uuid4()
    # The forged tenant also owns the user, so the pre-fix membership check
    # would have passed: only the DB-resolved authority refuses this.
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=attacker_tenant)

    await harness.run(_event(metadata_tenant=str(attacker_tenant)))

    harness.crud.create.assert_not_awaited()
    harness.fetch_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_tenant_disagreement_is_logged() -> None:
    """A refusal that does not say which tenant was aimed at is unactionable."""
    real_tenant = uuid4()
    attacker_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=attacker_tenant)

    await harness.run(_event(metadata_tenant=str(attacker_tenant)))

    mismatches = [r for r in harness.logger.loud() if "disagrees" in r[1]]
    assert mismatches, f"metadata mismatch not surfaced: {harness.logger.records}"
    _, _, kwargs = mismatches[0]
    assert kwargs["claimed_tenant_id"] == str(attacker_tenant)
    assert kwargs["db_tenant_id"] == str(real_tenant)


@pytest.mark.asyncio
async def test_absent_metadata_tenant_is_decided_by_the_db_row() -> None:
    """With no claim to contradict it, the DB row alone mints the subscription."""
    real_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=real_tenant)

    await harness.run(_event(metadata_tenant=None))

    harness.crud.create.assert_awaited_once()
    assert harness.created.tenant_id == real_tenant
    assert not [r for r in harness.logger.loud() if "disagrees" in r[1]]


@pytest.mark.asyncio
async def test_unparseable_metadata_tenant_is_refused() -> None:
    """A malformed claim fails closed before any lookup or write happens."""
    real_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=real_tenant)

    await harness.run(_event(metadata_tenant="not-a-uuid"))

    harness.crud.create.assert_not_awaited()
    harness.plan_service.return_value.get_plan_tenant_id.assert_not_awaited()
    assert any("Invalid tenant_id" in r[1] for r in harness.logger.loud())


@pytest.mark.asyncio
async def test_stripe_key_is_resolved_for_the_db_tenant() -> None:
    """The outbound Stripe call is scoped to the DB-resolved tenant."""
    real_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=real_tenant)

    await harness.run(_event(metadata_tenant=str(real_tenant)))

    assert harness.resolve_key.await_args[0][1] == real_tenant


@pytest.mark.asyncio
async def test_membership_is_checked_against_the_db_tenant() -> None:
    """A user outside the plan's owning tenant is refused, not upgraded."""
    real_tenant = uuid4()
    foreign_user_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=foreign_user_tenant)

    await harness.run(_event(metadata_tenant=str(real_tenant)))

    harness.crud.create.assert_not_awaited()
    assert any("membership mismatch" in r[1] for r in harness.logger.loud())


@pytest.mark.asyncio
async def test_unowned_plan_acks_without_calling_stripe() -> None:
    """A plan row with no resolvable owner never reaches the Stripe fetch."""
    harness = _CheckoutHarness(plan_tenant=None, user_tenant=None)

    await harness.run(_event(metadata_tenant=None))

    harness.fetch_subscription.assert_not_awaited()
    harness.crud.create.assert_not_awaited()
    assert any("no owning tenant" in r[1] for r in harness.logger.loud())


@pytest.mark.asyncio
async def test_honest_metadata_takes_the_same_path() -> None:
    """The producer's honest metadata still mints — no regression."""
    real_tenant = uuid4()
    harness = _CheckoutHarness(plan_tenant=real_tenant, user_tenant=real_tenant)

    await harness.run(_event(metadata_tenant=str(real_tenant)))

    harness.crud.create.assert_awaited_once()
    assert harness.created.tenant_id == real_tenant
    assert not [r for r in harness.logger.loud() if "disagrees" in r[1]]
