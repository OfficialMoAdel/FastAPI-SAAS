"""Row-lock helpers for Stripe webhook subscription handlers.

P2-#12: every webhook handler repeats the same two-phase lock dance — a
bypass-context lookup by ``stripe_subscription_id`` to discover the tenant,
then a ``with_for_update`` re-lock by primary key inside the tenant context.
Extracting it here makes the invariant impossible to skip: no handler can
forget the ``with_for_update`` or capture row attributes after the bypass
session exits (the historical DetachedInstanceError / TOCTOU bug class).

Both helpers resolve shared names through the package object
(``_pkg.<name>``), preserving the late-binding semantics the tests rely on
(``mock.patch("...billing.webhook.<name>")`` keeps intercepting).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.billing import webhook as _pkg


class SubscriptionRef(NamedTuple):
    """Tenant-scoped identity captured from the bypass-context row lock.

    Attributes are read INSIDE the bypass session — reading them after it
    exits risks DetachedInstanceError (see the P1 FIX in
    handle_subscription_deleted).
    """

    tenant_id: Any
    user_id: Any
    subscription_id: Any


async def find_subscription_by_stripe_id(stripe_subscription_id: str) -> SubscriptionRef:
    """Lock the subscription row by Stripe id in a bypass context.

    P0-1: the bypass context is used ONLY for the raw row lookup (RLS
    bypass, no tenant ContextVar — the webhook route is tenant-exempt).
    Tenant-scoped CRUD must run inside job_tenant_context afterwards.

    Raises:
        ValueError: row not found — propagates so Stripe retries delivery.
    """
    async with _pkg.job_bypass_context("Webhook subscription lookup") as db:
        result = await db.execute(
            select(_pkg.crud_user_subscriptions.model)
            .where(_pkg.crud_user_subscriptions.model.stripe_subscription_id == stripe_subscription_id)
            .with_for_update()
        )
        db_subscription = result.scalar_one_or_none()

        if not db_subscription:
            _pkg.logger.warning(
                f"Subscription not found for stripe_subscription_id: {stripe_subscription_id}. "
                f"Raising error to trigger Stripe retry."
            )
            raise ValueError(f"Subscription not found for {stripe_subscription_id}")

        return SubscriptionRef(
            tenant_id=db_subscription.tenant_id,
            user_id=db_subscription.user_id,
            subscription_id=db_subscription.id,
        )


async def lock_subscription_by_id(db: AsyncSession, subscription_id: Any) -> Any:
    """Re-lock the subscription row by primary key inside the tenant context.

    Phase-2 of the lock dance: the bypass-context lock is released when that
    session exits, so the row must be re-verified and locked here to prevent
    TOCTOU (a concurrently canceled subscription must not be resurrected).

    Returns the locked row, or ``None`` when it vanished in the race gap.
    """
    lock_result = await db.execute(
        select(_pkg.crud_user_subscriptions.model)
        .where(_pkg.crud_user_subscriptions.model.id == subscription_id)
        .with_for_update()
    )
    return lock_result.scalar_one_or_none()


__all__ = ["SubscriptionRef", "find_subscription_by_stripe_id", "lock_subscription_by_id"]
