"""Subscription & Billing Records service layer.

Business logic for subscription plans, user subscriptions, and billing records.
"""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.logger import get_logger
from src.app.core.tenancy.context import get_current_tenant, require_active_tenant
from src.app.modules._base.exceptions import (
    ModelNotFoundError,
    StripeError,
    TenantContextError,
)
from src.app.modules._base.service import BaseService
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
        super().__init__(SubscriptionPlan, db)
        self.tenant_id = tenant_id

    @property
    def current_tenant_id(self) -> Any:
        if self.tenant_id is not None:
            return self.tenant_id
        current_tenant = get_current_tenant()
        return current_tenant.id if current_tenant is not None else None

    def _resolve_tenant(self, entity_name: str = "SubscriptionPlan", entity_id: Any = None) -> Any:
        """Resolve tenant with cross-tenant mismatch guard."""
        from src.app.core.tenancy.context import get_current_tenant as _get_ctx

        ctx = _get_ctx()
        ctx_id = ctx.id if ctx is not None else None
        if self.tenant_id is not None and ctx_id is not None and str(self.tenant_id) != str(ctx_id):
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        tid = self.tenant_id if self.tenant_id is not None else ctx_id
        if tid is None:
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        return tid

    def _require_tenant(self, entity_name: str = "SubscriptionPlan", entity_id: Any = None) -> Any:
        tid = self.current_tenant_id
        if tid is None:
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        return tid

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
        tid = self._require_tenant("SubscriptionPlan", plan_id)

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
            await crud_subscription_plans.get_multi(
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
        # Tenant-scoped ORM fetch: no unfiltered db.get oracle, str-safe compare
        tid = self._resolve_tenant("SubscriptionPlan", plan_id)

        from sqlalchemy import select as _select

        from src.app.core.db.tenant_crud import tenant_scope as _tscope

        stmt = _select(SubscriptionPlan).where(
            SubscriptionPlan.id == plan_id, _tscope(SubscriptionPlan, tid)
        )
        result = await self.db.execute(stmt)
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
        tid = self._require_tenant("SubscriptionPlan", plan_id)

        plan = await crud_subscription_plans.get(db=self.db, id=plan_id, tenant_id=tid)
        if plan is None:
            raise ModelNotFoundError("SubscriptionPlan", plan_id)
        await crud_subscription_plans.update(
            db=self.db,
            object={"is_active": False, "updated_at": datetime.now(UTC)},
            id=plan_id,
        )
        return {"message": "Subscription plan deactivated"}


class UserSubscriptionService(BaseService[UserSubscription]):
    """Service for managing user subscriptions."""
    def __init__(self, db: AsyncSession, tenant_id: int | UUID | None = None):
        super().__init__(UserSubscription, db)
        self.tenant_id = tenant_id

    @property
    def current_tenant_id(self) -> Any:
        if self.tenant_id is not None:
            return self.tenant_id
        current_tenant = get_current_tenant()
        return current_tenant.id if current_tenant is not None else None

    def _resolve_tenant(self, entity_name: str = "UserSubscription", entity_id: Any = None) -> Any:
        """Resolve tenant with cross-tenant mismatch guard."""
        from src.app.core.tenancy.context import get_current_tenant as _get_ctx

        ctx = _get_ctx()
        ctx_id = ctx.id if ctx is not None else None
        if self.tenant_id is not None and ctx_id is not None and str(self.tenant_id) != str(ctx_id):
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        tid = self.tenant_id if self.tenant_id is not None else ctx_id
        if tid is None:
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        return tid

    def _require_tenant(self, entity_name: str = "UserSubscription", entity_id: Any = None) -> Any:
        tid = self.current_tenant_id
        if tid is None:
            raise ModelNotFoundError(entity_name, entity_id if entity_id is not None else "tenant")
        return tid

    async def create_subscription(self, user_id: int, data: UserSubscriptionCreate) -> dict[str, Any]:
        """Create a user subscription."""
        tid = self._require_tenant("Tenant", "current")

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
        tid = self._require_tenant("UserSubscription", subscription_id)

        subscription = await crud_user_subscriptions.get(db=self.db, id=subscription_id, tenant_id=tid)
        if subscription is None:
            raise ModelNotFoundError("UserSubscription", subscription_id)
        return cast(dict[str, Any], subscription)

    async def get_user_subscriptions(self, user_id: int) -> dict[str, Any]:
        """Get all subscriptions for a user."""
        filters: dict[str, Any] = {"user_id": user_id}
        tid = self.current_tenant_id
        if tid is not None:
            filters["tenant_id"] = tid
        return cast(
            dict[str, Any],
            await crud_user_subscriptions.get_multi(
                db=self.db,
                schema_to_select=UserSubscriptionRead,
                **filters,
            ),
        )

    async def update_subscription(
        self, subscription_id: int, data: UserSubscriptionUpdate, user_id: int | None = None
    ) -> dict[str, Any]:
        """Update a subscription."""
        tid = self._require_tenant("UserSubscription", subscription_id)

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
        self, subscription_id: int, user_id: int
    ) -> dict[str, Any]:
        """Cancel a subscription at period end, enforcing user ownership.

        Ownership/tenant-isolation check lives here (not in the router) so
        every caller gets it by construction.

        Raises:
            ForbiddenException: If the subscription is not owned by the user.
        """
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        subscription = await self.get_subscription(subscription_id)
        sub_user_id = (
            subscription.get("user_id")
            if isinstance(subscription, dict)
            else getattr(subscription, "user_id", None)
        )
        if sub_user_id != user_id:
            raise ForbiddenException("Not authorized to cancel this subscription")
        return await self.cancel_subscription(subscription_id, user_id=user_id)

    async def cancel_subscription(self, subscription_id: int, user_id: int | None = None) -> dict[str, Any]:
        """Cancel a subscription at period end."""
        logger.info("Cancelling subscription", subscription_id=subscription_id)
        tid = self._require_tenant("UserSubscription", subscription_id)
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

        # T109: Update DB first but do not commit yet
        await crud_user_subscriptions.update(
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
        result = await crud_user_subscriptions.get(db=self.db, id=subscription_id)
        return dict(result) if result else {}


class BillingRecordService(BaseService[BillingRecord]):
    """Service for managing billing records."""

    def __init__(self, db: AsyncSession):
        super().__init__(BillingRecord, db)

    async def create_record(self, data: BillingRecordCreate) -> dict[str, Any]:
        """Create a billing record."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("Tenant", "current")

        record_data = data.model_dump()
        record_data["tenant_id"] = tenant.id
        create_obj = BillingRecordCreateInternal(**record_data)
        return cast(
            dict[str, Any],
            await crud_billing_records.create(db=self.db, object=create_obj, schema_to_select=BillingRecordRead),
        )

    async def get_record(self, record_id: int) -> dict[str, Any]:
        """Get a billing record by ID."""
        tenant = get_current_tenant()
        if tenant is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        record = await crud_billing_records.get(db=self.db, id=record_id, tenant_id=tenant.id)
        if record is None:
            raise ModelNotFoundError("BillingRecord", record_id)

        return cast(dict[str, Any], record)

    async def get_record_for_user(self, record_id: int, user_id: int) -> dict[str, Any]:
        """Get a billing record, enforcing user ownership.

        Ownership check lives here (not in the router) so every caller gets
        it by construction.

        Raises:
            ForbiddenException: If the record is not owned by the user.
        """
        from src.app.core.exceptions.http_exceptions import ForbiddenException

        record = await self.get_record(record_id)
        record_user_id = (
            record.get("user_id") if isinstance(record, dict) else getattr(record, "user_id", None)
        )
        if record_user_id != user_id:
            raise ForbiddenException("Not authorized to view this record")
        return cast(dict[str, Any], record)

    async def get_user_records(
        self, user_id: int, page: int = 1, limit: int = 20, status: str | None = None
    ) -> dict[str, Any]:
        """Get paginated billing records for a user."""
        tenant = get_current_tenant()
        if tenant is None:
            raise TenantContextError("Tenant context required")
        offset = (page - 1) * limit
        filters: dict[str, Any] = {"user_id": user_id, "tenant_id": tenant.id}

        # T105: Add billing record status filtering
        if status is not None:
            filters["status"] = status

        return cast(
            dict[str, Any],
            await crud_billing_records.get_multi(
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
