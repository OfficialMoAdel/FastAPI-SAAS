"""Subscription & Billing Records service layer.

Business logic for subscription plans, user subscriptions, and billing records.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.scoped import execute_scoped
from src.app.core.logger import get_logger
from src.app.core.tenancy.context import (
    require_active_tenant,
    resolve_tenant_id,
)
from src.app.core.tenancy.scope import ExplicitTenant
from src.app.modules._base.service import BaseService, fetch_page_with_total
from src.app.modules.billing.crud import (
    crud_billing_records,
    crud_subscription_plans,
    crud_user_subscriptions,
)
from src.app.modules.billing.models import (
    BillingRecord,
    SubscriptionPlan,
    UserSubscription,
)
from src.app.modules.billing.schemas import (
    BillingRecordCreate,
    BillingRecordCreateInternal,
    BillingRecordRead,
    SubscriptionPlanCreate,
    SubscriptionPlanCreateInternal,
    SubscriptionPlanRead,
    SubscriptionPlanUpdate,
    UserSubscriptionCreate,
    UserSubscriptionCreateInternal,
    UserSubscriptionRead,
    UserSubscriptionUpdate,
)
from src.app.shared.exceptions import ForbiddenError, ModelNotFoundError, StripeError

if TYPE_CHECKING:
    from src.app.clients.billing.stripe import PaymentGateway

logger = get_logger(__name__)


def _subscription_plan_to_dict(plan: SubscriptionPlan) -> dict[str, Any]:
    """Convert SubscriptionPlan ORM object to response dict directly."""
    return {
        "id": plan.id,
        "tenant_id": plan.tenant_id,
        "name": plan.name,
        "description": plan.description,
        "sort_order": plan.sort_order,
        "stripe_price_id_monthly": plan.stripe_price_id_monthly,
        "stripe_price_id_yearly": plan.stripe_price_id_yearly,
        "features": plan.features,
        "is_active": plan.is_active,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


class SubscriptionPlanService(BaseService[SubscriptionPlan]):
    """Service for managing subscription plans."""

    def __init__(self, db: AsyncSession, tenant_id: int | UUID | None = None):
        super().__init__(SubscriptionPlan, db, select_schema=SubscriptionPlanRead)
        self.tenant_id = tenant_id

    @property
    def current_tenant_id(self) -> Any:
        """Effective tenant id — read-only view over the ONE seam.

        Returns the context tenant when bound, else the construction value,
        else ``None`` (``fail_closed=False``). Callers that must not proceed
        without a tenant use :meth:`_resolve_tenant` instead.
        """
        return resolve_tenant_id(
            self.tenant_id,
            entity_name="Tenant",
            entity_id="current",
            fail_closed=False,
        )

    def _resolve_tenant(self, entity_name: str = "SubscriptionPlan", entity_id: Any = None) -> Any:
        """Resolve tenant via the ONE seam (context wins; mismatch soft-404s)."""
        return resolve_tenant_id(self.tenant_id, entity_name=entity_name, entity_id=entity_id)

    async def create_plan(self, data: SubscriptionPlanCreate) -> dict[str, Any]:
        """Create a new subscription plan."""
        require_active_tenant()
        tid = self._resolve_tenant("SubscriptionPlan", "create")

        plan_data = data.model_dump()
        plan_data["tenant_id"] = tid
        create_obj = SubscriptionPlanCreateInternal(**plan_data)
        return cast(
            dict[str, Any],
            await crud_subscription_plans.create(
                db=self.db,
                object=create_obj,
                schema_to_select=SubscriptionPlanRead,
            ),
        )

    async def get_plan(self, plan_id: int) -> dict[str, Any]:
        """Get a subscription plan by ID."""
        tid = self._resolve_tenant("SubscriptionPlan", plan_id)

        plan = await crud_subscription_plans.get(db=self.db, id=plan_id, tenant_id=tid)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        return cast(dict[str, Any], plan)

    async def get_plans(self, page: int = 1, limit: int = 20, is_active: bool | None = None) -> dict[str, Any]:
        """Get paginated subscription plans (global catalog — preserved behavior)."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if is_active is not None:
            filters["is_active"] = is_active
        return cast(
            dict[str, Any],
            await fetch_page_with_total(
                crud_subscription_plans,
                self.db,
                offset=offset,
                limit=limit,
                sort_columns=["sort_order"],
                schema_to_select=SubscriptionPlanRead,
                **filters,
            ),
        )

    async def update_plan(self, plan_id: int, data: SubscriptionPlanUpdate) -> dict[str, Any]:
        """Update a subscription plan.

        P2-3: Uses session refresh instead of second DB roundtrip.
        """
        # Tenant-scoped ORM fetch: no unfiltered db.get oracle, str-safe compare
        tid = self._resolve_tenant("SubscriptionPlan", plan_id)

        from sqlalchemy import select as _select

        stmt = _select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id)
        result = await execute_scoped(
            stmt,
            db=self.db,
            model=SubscriptionPlan,
            scope=ExplicitTenant(
                tenant_id=tid, reason="legacy-criterion:SubscriptionPlan"
            ),
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)
        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)

        # Apply updates to ORM object
        for key, value in update_data.items():
            setattr(plan, key, value)

        await self.db.commit()
        await self.db.refresh(plan)

        return _subscription_plan_to_dict(plan)

    async def delete_plan(self, plan_id: int) -> dict[str, str]:
        """Soft delete a subscription plan."""
        tid = self._resolve_tenant("SubscriptionPlan", plan_id)

        plan = await crud_subscription_plans.get(db=self.db, id=plan_id, tenant_id=tid)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)
        await crud_subscription_plans.update(
            db=self.db,
            object={"is_active": False, "updated_at": datetime.now(UTC)},
            id=plan_id,
        )
        return {"message": "Subscription plan deactivated"}

    async def get_plan_tenant_id(self, plan_id: int) -> Any | None:
        """Return the owning tenant of a plan, unscoped, else None (I10 seam).

        Webhook handlers run in a bypass context (no tenant middleware) and
        must validate that a checkout's claimed tenant actually owns the
        plan before minting a subscription — they need the plan's OWNED
        tenant, not a context-scoped lookup.
        """
        plan = await crud_subscription_plans.get(db=self.db, id=plan_id, _allow_unscoped=True)
        if plan is None:
            return None
        return plan.get("tenant_id") if isinstance(plan, dict) else getattr(plan, "tenant_id", None)


class UserSubscriptionService(BaseService[UserSubscription]):
    """Service for managing user subscriptions."""

    def __init__(self, db: AsyncSession, tenant_id: int | UUID | None = None):
        super().__init__(UserSubscription, db, select_schema=UserSubscriptionRead)
        self.tenant_id = tenant_id

    @property
    def current_tenant_id(self) -> Any:
        """Effective tenant id (context wins; explicit construction value otherwise).

        Read-only compatibility view over the ONE seam — see
        :class:`SubscriptionPlanService`.
        """
        return resolve_tenant_id(
            self.tenant_id,
            entity_name="Tenant",
            entity_id="current",
            fail_closed=False,
        )

    def _resolve_tenant(self, entity_name: str = "UserSubscription", entity_id: Any = None) -> Any:
        """Resolve tenant via the ONE seam (context wins; mismatch soft-404s)."""
        return resolve_tenant_id(self.tenant_id, entity_name=entity_name, entity_id=entity_id)

    async def create_subscription(self, user_id: int, data: UserSubscriptionCreate) -> dict[str, Any]:
        """Create a user subscription."""
        tid = self._resolve_tenant("Tenant", "current")

        logger.info("Creating subscription for user in tenant", user_id=user_id, id=tid)
        sub_data = data.model_dump()
        sub_data["tenant_id"] = tid
        sub_data["user_id"] = user_id
        create_obj = UserSubscriptionCreateInternal(**sub_data)
        result = cast(
            dict[str, Any],
            await crud_user_subscriptions.create(
                db=self.db,
                object=create_obj,
                schema_to_select=UserSubscriptionRead,
            ),
        )
        logger.info("Subscription created:", val=result.get("id") if result else None)
        return result

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Get a subscription by ID."""
        tid = self._resolve_tenant("UserSubscription", subscription_id)

        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tid)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)
        return cast(dict[str, Any], subscription)

    async def get_user_subscriptions(
        self, user_id: int, page: int | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """Get subscriptions for a user, optionally paginated.

        When page/limit are omitted the full list is returned (legacy
        behavior for internal callers); pass both for a single page.

        Tenant scoping mirrors :meth:`_resolve_tenant` (the ONE seam): a
        tenant-less worker/system path keeps the legacy unfiltered list.
        """
        filters: dict[str, Any] = {"user_id": user_id}
        # Context wins over the construction value (same rule as the
        # property); neither present keeps the legacy unfiltered list.
        effective_tenant = self.current_tenant_id
        if effective_tenant is not None:
            filters["tenant_id"] = effective_tenant
        paging: dict[str, Any] = {}
        if page is not None and limit is not None:
            paging["offset"] = (page - 1) * limit
            paging["limit"] = limit
        return cast(
            dict[str, Any],
            await fetch_page_with_total(
                crud_user_subscriptions,
                self.db,
                schema_to_select=UserSubscriptionRead,
                **filters,
                **paging,
            ),
        )

    async def update_subscription(
        self, subscription_id: int, data: UserSubscriptionUpdate, user_id: int | None = None
    ) -> dict[str, Any]:
        """Update a subscription."""
        tid = self._resolve_tenant("UserSubscription", subscription_id)

        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tid)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)
        if (
            user_id is not None
            and (
                subscription.get("user_id")
                if isinstance(subscription, dict)
                else getattr(subscription, "user_id", None)
            )
            != user_id
        ):
            raise ModelNotFoundError("UserSubscription", subscription_id)

        update_data = data.model_dump(exclude_unset=True)
        update_data["updated_at"] = datetime.now(UTC)
        await crud_user_subscriptions.update(db=self.db, object=update_data, id=subscription_id)
        result = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}

    async def cancel_subscription_for_user(
        self, subscription_id: int, user_id: int, *, is_superuser: bool = False
    ) -> dict[str, Any]:
        """Cancel a subscription at period end, enforcing user ownership.

        Ownership/tenant-isolation check lives here (not in the router) so
        every caller gets it by construction. Item 8: enforced via the ONE
        ownership seam (:func:`require_resource_ownership`).

        Raises:
            ForbiddenError: If the subscription is not owned by the user.
        """
        from src.app.api.deps.request_context import require_resource_ownership

        subscription = await self.get_subscription(subscription_id)
        sub_user_id = (
            subscription.get("user_id") if isinstance(subscription, dict) else getattr(subscription, "user_id", None)
        )
        try:
            require_resource_ownership(sub_user_id, user_id, is_superuser)
        except ForbiddenError:
            raise ForbiddenError("Not authorized to cancel this subscription") from None
        return await self.cancel_subscription(subscription_id, user_id=user_id)

    async def cancel_subscription(self, subscription_id: int, user_id: int | None = None) -> dict[str, Any]:
        """Cancel a subscription at period end."""
        logger.info("Cancelling subscription", subscription_id=subscription_id)
        tid = self._resolve_tenant("UserSubscription", subscription_id)
        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tid)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        if (
            user_id is not None
            and (
                subscription.get("user_id")
                if isinstance(subscription, dict)
                else getattr(subscription, "user_id", None)
            )
            != user_id
        ):
            raise ModelNotFoundError("UserSubscription", subscription_id)

        # B14: call Stripe BEFORE taking the DB row lock. FastCRUD's
        # update(commit=False) executes the UPDATE immediately, so the old
        # DB-first order held the row lock across the Stripe HTTP call (up to
        # 3 retries with long timeouts), stalling every webhook handler that
        # SELECTs ... FOR UPDATE on the same subscription. If the process dies
        # between the Stripe call and the DB commit below, the
        # customer.subscription.updated webhook re-syncs cancel_at_period_end.
        stripe_subscription_id = (
            subscription.get("stripe_subscription_id")
            if isinstance(subscription, dict)
            else getattr(subscription, "stripe_subscription_id", None)
        )
        if stripe_subscription_id:
            tenant_id = (
                subscription.get("tenant_id")
                if isinstance(subscription, dict)
                else getattr(subscription, "tenant_id", None)
            )
            from src.app.clients.billing.stripe import get_payment_gateway

            gateway = get_payment_gateway(self.db)
            try:
                await gateway.modify_subscription(
                    stripe_subscription_id,
                    tenant_id=tenant_id,
                    cancel_at_period_end=True,
                )
                logger.info(
                    "Stripe subscription marked for cancellation", stripe_subscription_id=stripe_subscription_id
                )
            except StripeError:
                await self.db.rollback()
                raise
            except Exception as e:
                logger.error("Failed to cancel Stripe subscription", error=e)
                await self.db.rollback()
                raise StripeError(f"Failed to cancel subscription in Stripe: {str(e)}") from e

        # Short DB transaction: lock held only for the UPDATE + COMMIT.
        await crud_user_subscriptions.update(
            db=self.db,
            object={
                "cancel_at_period_end": True,
                "updated_at": datetime.now(UTC),
            },
            id=subscription_id,
            commit=False,
        )

        await self.db.commit()
        result = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}


class BillingRecordService(BaseService[BillingRecord]):
    """Service for managing billing records."""

    def __init__(self, db: AsyncSession):
        super().__init__(BillingRecord, db, select_schema=BillingRecordRead)

    async def create_record(self, data: BillingRecordCreate) -> dict[str, Any]:
        """Create a billing record."""
        tid = resolve_tenant_id(None, entity_name="Tenant", entity_id="current")

        record_data = data.model_dump()
        record_data["tenant_id"] = tid
        create_obj = BillingRecordCreateInternal(**record_data)
        return cast(
            dict[str, Any],
            await crud_billing_records.create(db=self.db, object=create_obj, schema_to_select=BillingRecordRead),
        )

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        tid = resolve_tenant_id(None, entity_name="BillingRecord", entity_id=record_id)

        record = await crud_billing_records.get(db=self.db, id=record_id, tenant_id=tid)
        if record is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        return cast(dict[str, Any], record)

    async def get_record_for_user(
        self, record_id: int, user_id: int, *, is_superuser: bool = False
    ) -> dict[str, Any]:
        """Get a billing record, enforcing user ownership.

        Ownership check lives here (not in the router) so every caller gets
        it by construction. Item 8: enforced via the ONE ownership seam
        (:func:`require_resource_ownership`).

        Raises:
            ForbiddenError: If the record is not owned by the user.
        """
        from src.app.api.deps.request_context import require_resource_ownership

        record = await self.get_record(record_id)
        record_user_id = record.get("user_id") if isinstance(record, dict) else getattr(record, "user_id", None)
        try:
            require_resource_ownership(record_user_id, user_id, is_superuser)
        except ForbiddenError:
            raise ForbiddenError("Not authorized to view this record") from None
        return cast(dict[str, Any], record)

    async def get_user_records(
        self, user_id: int, page: int = 1, limit: int = 20, status: str | None = None
    ) -> dict[str, Any]:
        """Get paginated billing records for a user."""
        # Item 7: fail-closed via the tenancy seam (missing/inactive context
        # raises TenantContextError) instead of an ad-hoc None check.
        tenant = require_active_tenant()
        offset = (page - 1) * limit
        filters: dict[str, Any] = {"user_id": user_id, "tenant_id": tenant.id}

        # T105: Add billing record status filtering
        if status is not None:
            filters["status"] = status

        return cast(
            dict[str, Any],
            await fetch_page_with_total(
                crud_billing_records,
                self.db,
                offset=offset,
                limit=limit,
                sort_columns=["created_at"],
                sort_orders=["desc"],
                schema_to_select=BillingRecordRead,
                **filters,
            ),
        )


def get_subscription_plan_service(db: AsyncSession) -> SubscriptionPlanService:
    """Get SubscriptionPlanService instance."""
    return SubscriptionPlanService(db)



def get_user_subscription_service(db: AsyncSession) -> UserSubscriptionService:
    """Get UserSubscriptionService instance."""
    return UserSubscriptionService(db)



def get_billing_record_service(db: AsyncSession) -> BillingRecordService:
    """Get billing record service instance."""
    return BillingRecordService(db)


# =============================================================================
# Webhook-facing seams (I10)
# =============================================================================
# Checkout webhook handlers depend ONLY on billing/lifecycle.py + this
# module — they no longer import the Stripe gateway adapter,
# CheckoutService, billing crud, or users.models directly. These wrappers
# are the sanctioned doors for the two outbound calls a checkout handler
# needs.


async def resolve_tenant_stripe_api_key(db: AsyncSession, tenant_id: UUID) -> str | None:
    """Resolve a tenant's Stripe API key for webhook paths (I10 seam).

    B15: per-tenant Stripe accounts — the checkout handler resolves the
    tenant's own key before any Stripe call. Delegates to the shared
    ``CheckoutService.resolve_api_key`` seam (single gateway-key
    construction point, Item 10); lazy import avoids the checkout_service
    ↔ subscription_service import cycle.
    """
    from src.app.modules.billing.checkout_service import CheckoutService

    return await CheckoutService(db).resolve_api_key(tenant_id)


async def fetch_stripe_subscription(api_key: str | None, stripe_subscription_id: str) -> Any:
    """Retrieve a Stripe subscription via the payment-gateway adapter (I10 seam).

    The ONLY place a billing webhook handler should reach Stripe for
    subscription data; keeps the ``src.app.clients`` adapter boundary out
    of handler code. Returns the adapter's ``stripe.Subscription``
    (dict-like) object.
    """
    from src.app.clients.billing.stripe import get_payment_gateway

    return await get_payment_gateway(api_key=api_key).retrieve_subscription(stripe_subscription_id)

async def retrieve_subscription_for_tenant(
    db: AsyncSession,
    tenant_id: UUID,
    stripe_subscription_id: str,
    *,
    gateway_factory: Callable[..., PaymentGateway] | None = None,
) -> Any:
    """Retrieve a Stripe subscription for a tenant through ONE gateway seam.

    Fuses the webhook two-step (``resolve_tenant_stripe_api_key`` +
    ``fetch_stripe_subscription``) so the resolved key never crosses a module
    boundary as a naked string: resolve → build → retrieve happen here and
    the handler makes one call. ``gateway_factory`` overrides gateway
    construction (called as ``factory(api_key=...)``; fakes inject here in
    tests). The default path is identical to the old two-step — same resolve
    seam, same ``fetch_stripe_subscription`` primitive.
    """
    from src.app.modules.billing.checkout_service import CheckoutService

    checkout = (
        CheckoutService(db, gateway_factory=gateway_factory)
        if gateway_factory is not None
        else CheckoutService(db)
    )
    api_key = await checkout.resolve_api_key(tenant_id)
    if gateway_factory is None:
        return await fetch_stripe_subscription(api_key, stripe_subscription_id)
    return await gateway_factory(api_key=api_key).retrieve_subscription(stripe_subscription_id)
