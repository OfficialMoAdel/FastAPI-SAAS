import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.billing.crud import crud_stripe_settings
from src.app.modules.billing.schemas import (
    StripeSettingsCreate,
    StripeSettingsCreateInternal,
    StripeSettingsRead,
    StripeSettingsUpdate,
)


@pytest.mark.asyncio
async def test_create_get_stripe_settings(db_session: AsyncSession, tenant):
    """Test creating and retrieving Stripe settings."""
    settings_in = StripeSettingsCreate(
        stripe_customer_id="cus_test_123",
        stripe_account_id="acct_test_123",
        webhook_secret="whsec_test_123",
        is_active=True,
    )

    data = settings_in.model_dump()
    data["tenant_id"] = tenant.id

    # Use Internal schema for creation
    obj_in = StripeSettingsCreateInternal(**data)

    # FastCRUD v0.20.0 requires schema_to_select to return data
    settings = await crud_stripe_settings.create(db=db_session, object=obj_in, schema_to_select=StripeSettingsRead)

    assert settings["stripe_customer_id"] == "cus_test_123"
    assert settings["tenant_id"] == tenant.id
    assert settings["is_active"] is True

    # Get
    fetched = await crud_stripe_settings.get(db=db_session, id=settings["id"])
    assert fetched is not None
    # CRUD get returns dict by default in this project configuration
    assert fetched["stripe_customer_id"] == "cus_test_123"

    # Get by tenant
    fetched_by_tenant = await crud_stripe_settings.get(db=db_session, tenant_id=tenant.id)
    assert fetched_by_tenant is not None
    assert fetched_by_tenant["id"] == settings["id"]

    # Cleanup
    await crud_stripe_settings.db_delete(db=db_session, id=settings["id"])


@pytest.mark.asyncio
async def test_update_stripe_settings(db_session: AsyncSession, tenant):
    """Test updating Stripe settings."""
    # Ensure cleanup from previous tests if any (though unlikely if order is guaranteed)
    existing = await crud_stripe_settings.get(db=db_session, tenant_id=tenant.id)
    if existing:
        await crud_stripe_settings.db_delete(db=db_session, id=existing["id"])

    # Setup
    settings_in = StripeSettingsCreate(
        stripe_customer_id="cus_original",
        is_active=True,
    )
    data = settings_in.model_dump()
    data["tenant_id"] = tenant.id
    obj_in = StripeSettingsCreateInternal(**data)
    settings = await crud_stripe_settings.create(db=db_session, object=obj_in, schema_to_select=StripeSettingsRead)

    # Update
    update_data = StripeSettingsUpdate(
        # stripe_customer_id="cus_updated", # Not allowed in update schema
        is_active=False
    )
    # CRUD update takes (db, object=update_data_dict/schema, id=xxx)
    settings_id = settings["id"]
    await crud_stripe_settings.update(db=db_session, object=update_data, id=settings_id)

    # FastCRUD update returns None by default, verification happens via fetch

    # Verify persistence
    fetched = await crud_stripe_settings.get(db=db_session, id=settings_id)
    assert fetched["is_active"] is False

    # Cleanup
    await crud_stripe_settings.db_delete(db=db_session, id=settings_id)


@pytest.mark.asyncio
async def test_stripe_settings_multi_tenancy(db_session: AsyncSession, tenant):
    """Test that settings are isolated by tenant."""
    # Cleanup tenant 1
    existing = await crud_stripe_settings.get(db=db_session, tenant_id=tenant.id)
    if existing:
        await crud_stripe_settings.db_delete(db=db_session, id=existing["id"])

    # Create settings for tenant 1
    data1 = {"stripe_customer_id": "cus_t1", "tenant_id": tenant.id, "is_active": True}
    obj1 = StripeSettingsCreateInternal(**data1)
    s1 = await crud_stripe_settings.create(db=db_session, object=obj1, schema_to_select=StripeSettingsRead)
    s1_id = s1["id"]  # Capture ID before other operations expire the object

    # We need a second tenant.
    # Check if tenant 2 exists
    from sqlalchemy import select

    from src.app.modules.tenants.models import Tenant

    res = await db_session.execute(select(Tenant).where(Tenant.slug == "tenant-2"))
    tenant2 = res.scalar_one_or_none()

    if not tenant2:
        tenant2 = Tenant(name="Tenant 2", slug="tenant-2", is_active=True)
        db_session.add(tenant2)
        await db_session.commit()
        await db_session.refresh(tenant2)

    tenant2_id = tenant2.id

    # Cleanup tenant 2 settings
    existing2 = await crud_stripe_settings.get(db=db_session, tenant_id=tenant2_id)
    if existing2:
        await crud_stripe_settings.db_delete(db=db_session, id=existing2["id"])

    data2 = {"stripe_customer_id": "cus_t2", "tenant_id": tenant2_id, "is_active": True}
    obj2 = StripeSettingsCreateInternal(**data2)
    s2 = await crud_stripe_settings.create(db=db_session, object=obj2, schema_to_select=StripeSettingsRead)
    s2_id = s2["id"]

    # Verify isolation
    # Get for tenant 1 should return s1
    f1 = await crud_stripe_settings.get(db=db_session, tenant_id=tenant.id)
    assert f1["id"] == s1_id
    assert f1["stripe_customer_id"] == "cus_t1"

    # Get for tenant 2 should return s2
    f2 = await crud_stripe_settings.get(db=db_session, tenant_id=tenant2_id)
    assert f2["id"] == s2_id
    assert f2["stripe_customer_id"] == "cus_t2"

    # Cleanup
    await crud_stripe_settings.db_delete(db=db_session, id=s1_id)
    await crud_stripe_settings.db_delete(db=db_session, id=s2_id)
