"""Subscription & Billing Records service layer.

Business logic for subscription plans, user subscriptions, and billing records.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

import src.app.modules.billing.service as _svc
from src.app.core.logger import get_logger
from src.app.modules._base.exceptions import (
    ModelNotFoundError,
    StripeError,
    TenantContextError,
)
from src.app.modules._base.service import BaseService
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

    def __init__(self, db: AsyncSession):
        super().__init__(SubscriptionPlan, db)

    async def create_plan(self, data: SubscriptionPlanCreate) -> dict[str, Any]:
        """Create a new subscription plan."""
        tenant = _svc._require_active_tenant()

        plan_data = data.model_dump()
        plan_data["tenant_id"] = tenant.id
        create_obj = SubscriptionPlanCreateInternal(**plan_data)
        return cast(
            dict[str, Any],
            await _svc.crud_subscription_plans.create(
                db=self.db,
                object=create_obj,
                schema_to_select=SubscriptionPlanRead,
            ),
        )

    async def get_plan(self, plan_id: int) -> dict[str, Any]:
        """Get a subscription plan by ID."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        plan = await _svc.crud_subscription_plans.get(db=self.db, id=plan_id, tenant_id=tenant.id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        return cast(dict[str, Any], plan)

    async def get_plans(self, page: int = 1, limit: int = 20, is_active: bool | None = None) -> dict[str, Any]:
        """Get paginated subscription plans."""
        offset = (page - 1) * limit
        filters: dict[str, Any] = {}
        if is_active is not None:
            filters["is_active"] = is_active

        # T063: Sort by sort_order
        return cast(
            dict[str, Any],
            await _svc.crud_subscription_plans.get_multi(
                db=self.db,
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
        # Fetch ORM object directly for in-place update
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        plan = await self.db.get(SubscriptionPlan, plan_id)
        if plan is None or plan.tenant_id != tenant.id:
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
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        plan = await _svc.crud_subscription_plans.get(db=self.db, id=plan_id, tenant_id=tenant.id)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)

        await _svc.crud_subscription_plans.update(
            db=self.db,
            object={"is_active": False, "updated_at": datetime.now(UTC)},
            id=plan_id,
        )
        return {"message": "Subscription plan deactivated"}


class UserSubscriptionService(BaseService[UserSubscription]):
    """Service for managing user subscriptions."""

    def __init__(self, db: AsyncSession):
        super().__init__(UserSubscription, db)

    async def create_subscription(self, user_id: int, data: UserSubscriptionCreate) -> dict[str, Any]:
        """Create a user subscription."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        logger.info("Creating subscription for user in tenant", user_id=user_id, id=tenant.id)
        sub_data = data.model_dump()
        sub_data["tenant_id"] = tenant.id
        sub_data["user_id"] = user_id
        create_obj = UserSubscriptionCreateInternal(**sub_data)
        result = cast(
            dict[str, Any],
            await _svc.crud_user_subscriptions.create(
                db=self.db,
                object=create_obj,
                schema_to_select=UserSubscriptionRead,
            ),
        )
        logger.info("Subscription created:", val=result.get("id") if result else None)
        return result

    async def get_subscription(self, subscription_id: int) -> dict[str, Any]:
        """Get a subscription by ID."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        subscription = await _svc.crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tenant.id)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        return cast(dict[str, Any], subscription)

    async def get_user_subscriptions(self, user_id: int) -> dict[str, Any]:
        """Get all subscriptions for a user."""
        filters: dict[str, Any] = {"user_id": user_id}
        tenant = _svc.get_current_tenant()
        if tenant:
            filters["tenant_id"] = tenant.id

        return cast(
            dict[str, Any],
            await _svc.crud_user_subscriptions.get_multi(
                db=self.db,
                schema_to_select=UserSubscriptionRead,
                **filters,
            ),
        )

    async def update_subscription(
        self, subscription_id: int, data: UserSubscriptionUpdate, user_id: int | None = None
    ) -> dict[str, Any]:
        """Update a subscription."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        subscription = await _svc.crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tenant.id)
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
        await _svc.crud_user_subscriptions.update(db=self.db, object=update_data, id=subscription_id)
        result = await _svc.crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}

    async def cancel_subscription(self, subscription_id: int, user_id: int | None = None) -> dict[str, Any]:
        """Cancel a subscription at period end."""
        logger.info("Cancelling subscription", subscription_id=subscription_id)
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)

        subscription = await _svc.crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tenant.id)
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

        # T109: Update DB first but do not commit yet
        await _svc.crud_user_subscriptions.update(
            db=self.db,
            object={
                "cancel_at_period_end": True,
                "updated_at": datetime.now(UTC),
            },
            id=subscription_id,
            commit=False,
        )

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
            from src.app.modules.billing.gateway import get_payment_gateway

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

        await self.db.commit()
        result = await _svc.crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}


class BillingRecordService(BaseService[BillingRecord]):
    """Service for managing billing records."""

    def __init__(self, db: AsyncSession):
        super().__init__(BillingRecord, db)

    async def create_record(self, data: BillingRecordCreate) -> dict[str, Any]:
        """Create a billing record."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        record_data = data.model_dump()
        record_data["tenant_id"] = tenant.id
        create_obj = BillingRecordCreateInternal(**record_data)
        return cast(
            dict[str, Any],
            await _svc.crud_billing_records.create(db=self.db, object=create_obj, schema_to_select=BillingRecordRead),
        )

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        record = await _svc.crud_billing_records.get(db=self.db, id=record_id, tenant_id=tenant.id)
        if record is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        return cast(dict[str, Any], record)

    async def get_user_records(
        self, user_id: int, page: int = 1, limit: int = 20, status: str | None = None
    ) -> dict[str, Any]:
        """Get paginated billing records for a user."""
        tenant = _svc.get_current_tenant()
        if tenant is None:
            raise TenantContextError("Tenant context required")
        offset = (page - 1) * limit
        filters: dict[str, Any] = {"user_id": user_id, "tenant_id": tenant.id}

        # T105: Add billing record status filtering
        if status is not None:
            filters["status"] = status

        return cast(
            dict[str, Any],
            await _svc.crud_billing_records.get_multi(
                db=self.db,
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
