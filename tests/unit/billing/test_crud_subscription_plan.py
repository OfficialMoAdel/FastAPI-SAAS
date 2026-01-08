import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.app.modules.billing.crud import crud_subscription_plans
from src.app.modules.billing.schemas import (
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionPlanCreateInternal,
)


@pytest.mark.asyncio
async def test_create_get_subscription_plan(db_session: AsyncSession, tenant):
    """Test creating and retrieving a subscription plan."""
    plan_in = SubscriptionPlanCreate(
        name="Pro Plan",
        description="Best plan ever",
        price_monthly_cents=1000,
        price_yearly_cents=10000,
        currency="usd",
        sort_order=1,
        is_active=True,
    )

    data = plan_in.model_dump()
    data["tenant_id"] = tenant.id

    # Use Internal schema for creation to include tenant_id
    obj_in = SubscriptionPlanCreateInternal(**data)

    plan = await crud_subscription_plans.create(db=db_session, object=obj_in)

    assert plan.name == "Pro Plan"
    assert plan.tenant_id == tenant.id
    assert plan.price_monthly_cents == 1000

    # Get
    fetched = await crud_subscription_plans.get(db=db_session, id=plan.id)
    assert fetched is not None
    assert fetched["name"] == "Pro Plan"

    # Cleanup
    await crud_subscription_plans.db_delete(db=db_session, id=plan.id)


@pytest.mark.asyncio
async def test_update_subscription_plan(db_session: AsyncSession, tenant):
    """Test updating a subscription plan."""
    # Setup
    plan_in = SubscriptionPlanCreate(
        name="Original Plan", price_monthly_cents=500, price_yearly_cents=5000, currency="usd"
    )
    data = plan_in.model_dump()
    data["tenant_id"] = tenant.id
    obj_in = SubscriptionPlanCreateInternal(**data)
    plan = await crud_subscription_plans.create(db=db_session, object=obj_in)
    plan_id = plan.id

    # Update
    update_data = SubscriptionPlanUpdate(name="Updated Plan", price_monthly_cents=600)

    await crud_subscription_plans.update(db=db_session, object=update_data, id=plan_id)

    # Verify persistence
    fetched = await crud_subscription_plans.get(db=db_session, id=plan_id)
    assert fetched["name"] == "Updated Plan"
    assert fetched["price_monthly_cents"] == 600
    assert fetched["price_yearly_cents"] == 5000  # Unchanged

    # Cleanup
    await crud_subscription_plans.db_delete(db=db_session, id=plan_id)


@pytest.mark.asyncio
async def test_list_subscription_plans(db_session: AsyncSession, tenant):
    """Test listing subscription plans."""
    # Create two plans
    data1 = {
        "name": "Plan A",
        "price_monthly_cents": 100,
        "price_yearly_cents": 1000,
        "currency": "usd",
        "tenant_id": tenant.id,
    }
    obj1 = SubscriptionPlanCreateInternal(**data1)
    p1 = await crud_subscription_plans.create(db=db_session, object=obj1)
    p1_id = p1.id

    data2 = {
        "name": "Plan B",
        "price_monthly_cents": 200,
        "price_yearly_cents": 2000,
        "currency": "usd",
        "tenant_id": tenant.id,
    }
    obj2 = SubscriptionPlanCreateInternal(**data2)
    p2 = await crud_subscription_plans.create(db=db_session, object=obj2)
    p2_id = p2.id

    # List
    result = await crud_subscription_plans.get_multi(db=db_session, tenant_id=tenant.id)
    plans = result["data"]

    assert len(plans) >= 2
    ids = [p["id"] for p in plans]
    assert p1_id in ids
    assert p2_id in ids

    # Cleanup
    await crud_subscription_plans.db_delete(db=db_session, id=p1_id)
    await crud_subscription_plans.db_delete(db=db_session, id=p2_id)
