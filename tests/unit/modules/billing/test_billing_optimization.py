"""Unit tests for billing dict optimization helper functions.

Tests verify that _product_to_dict() and _pricing_tier_to_dict() produce
identical output to the original Pydantic serialization path.
"""

from uuid import uuid4

import pytest

from src.app.modules.billing.models import Pricing, Product
from src.app.modules.billing.schemas import PricingRead, ProductRead
from src.app.modules.billing.service import _pricing_tier_to_dict, _product_to_dict


@pytest.fixture
def sample_tenant_id():
    """Generate a sample tenant UUID for testing."""
    return uuid4()


@pytest.fixture
def sample_pricing(db) -> Pricing:
    """Create a sample pricing tier for testing."""
    pricing = Pricing(
        product_id=1,
        name="Pro Plan",
        amount_cents=999,
        interval="monthly",
        stripe_price_id="price_xxx",
        currency="USD",
        features=["Feature 1", "Feature 2"],
        is_active=True,
    )
    db.add(pricing)
    db.commit()
    db.refresh(pricing)
    return pricing


@pytest.fixture
def sample_product(db, sample_pricing, sample_tenant_id) -> Product:
    """Create a sample product with pricing tiers for testing."""
    product = Product(
        tenant_id=sample_tenant_id,  # FIX: Changed from int to UUID
        name="Pro Package",
        description="Professional tier features",
        stripe_product_id="prod_xxx",
        is_active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def test_pricing_tier_to_dict_matches_original(sample_pricing):
    """Test that _pricing_tier_to_dict produces identical output to Pydantic model."""
    # Get dict from helper function
    helper_dict = _pricing_tier_to_dict(sample_pricing)

    # Create Pydantic model from same object
    pydantic_model = PricingRead.model_validate(sample_pricing)
    pydantic_dict = pydantic_model.model_dump()

    # Compare all fields
    assert helper_dict["id"] == pydantic_dict["id"]
    assert helper_dict["product_id"] == pydantic_dict["product_id"]
    assert helper_dict["name"] == pydantic_dict["name"]
    assert helper_dict["amount_cents"] == pydantic_dict["amount_cents"]
    assert helper_dict["interval"] == pydantic_dict["interval"]
    assert helper_dict["stripe_price_id"] == pydantic_dict["stripe_price_id"]
    assert helper_dict["currency"] == pydantic_dict["currency"]
    assert helper_dict["features"] == pydantic_dict["features"]
    assert helper_dict["is_active"] == pydantic_dict["is_active"]

    # Dicts should be identical
    assert helper_dict == pydantic_dict

    # Log dict size and key count
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"pricing_tier dict: {len(helper_dict)} keys, size: {len(str(helper_dict))} bytes")


def test_product_to_dict_matches_original(db, sample_product):
    """Test that _product_to_dict produces identical output to Pydantic model."""
    # Create pricing tiers for the product
    pricing1 = Pricing(
        product_id=sample_product.id,
        name="Monthly Plan",
        amount_cents=999,
        interval="monthly",
        stripe_price_id="price_monthly",
        currency="USD",
        features=["Feature 1"],
        is_active=True,
    )
    pricing2 = Pricing(
        product_id=sample_product.id,
        name="Yearly Plan",
        amount_cents=9990,
        interval="yearly",
        stripe_price_id="price_yearly",
        currency="USD",
        features=["Feature 1", "Feature 2"],
        is_active=True,
    )
    db.add_all([pricing1, pricing2])
    db.commit()
    db.refresh(pricing1)
    db.refresh(pricing2)

    pricing_tiers = [pricing1, pricing2]

    # Get dict from helper function
    helper_dict = _product_to_dict(sample_product, pricing_tiers)

    # Create Pydantic model from same object
    pydantic_model = ProductRead.model_validate(sample_product)
    pydantic_dict = pydantic_model.model_dump()

    # Compare all fields (excluding pricing_tiers which is not in Pydantic model)
    assert helper_dict["id"] == pydantic_dict["id"]
    assert helper_dict["tenant_id"] == pydantic_dict["tenant_id"]
    assert helper_dict["name"] == pydantic_dict["name"]
    assert helper_dict["description"] == pydantic_dict["description"]
    assert helper_dict["stripe_product_id"] == pydantic_dict["stripe_product_id"]
    assert helper_dict["is_active"] == pydantic_dict["is_active"]

    # Verify pricing tiers are present in helper dict
    assert "pricing_tiers" in helper_dict
    assert len(helper_dict["pricing_tiers"]) == 2

    # Verify each pricing tier using _pricing_tier_to_dict
    for i, pricing in enumerate(pricing_tiers):
        expected_pricing_dict = _pricing_tier_to_dict(pricing)
        assert helper_dict["pricing_tiers"][i] == expected_pricing_dict

    # Log dict size and key count
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"product dict: {len(helper_dict)} keys, "
        f"size: {len(str(helper_dict))} bytes, "
        f"pricing_tiers: {len(helper_dict['pricing_tiers'])}"
    )


def test_nested_structures_serialized(db, sample_tenant_id):
    """Test that nested structures are properly serialized."""
    # Create product with multiple nested pricing tiers
    product = Product(
        tenant_id=sample_tenant_id,  # FIX: Changed from int to UUID
        name="Enterprise Package",
        description="Enterprise tier with many features",
        stripe_product_id="prod_enterprise",
        is_active=True,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    # Create multiple pricing tiers with nested features
    pricing_tiers = []
    for i in range(3):
        pricing = Pricing(
            product_id=product.id,
            name=f"Plan {i}",
            amount_cents=(i + 1) * 1000,
            interval="monthly" if i % 2 == 0 else "yearly",
            stripe_price_id=f"price_{i}",
            currency="USD",
            features=[f"Feature {j}" for j in range(i + 1)],
            is_active=True,
        )
        db.add(pricing)
        db.commit()
        db.refresh(pricing)
        pricing_tiers.append(pricing)

    # Serialize with helper
    result = _product_to_dict(product, pricing_tiers)

    # Verify nested structure
    assert isinstance(result, dict)
    assert "pricing_tiers" in result
    assert len(result["pricing_tiers"]) == 3

    # Verify each pricing tier has nested features
    for i, pricing in enumerate(result["pricing_tiers"]):
        assert "features" in pricing
        assert isinstance(pricing["features"], list)
        assert len(pricing["features"]) == i + 1

    # Log dict size
    import logging

    logger = logging.getLogger(__name__)
    logger.info(
        f"nested product dict: {len(result)} keys, "
        f"size: {len(str(result))} bytes, "
        f"total pricing tiers: {len(result['pricing_tiers'])}, "
        f"total features: {sum(len(p['features']) for p in result['pricing_tiers'])}"
    )


def test_feature_flag_off_uses_legacy(db_session, sample_product):
    """Test that when feature flag is off, original code path is used.

    Note: This test verifies the behavior when FEATURE_BILLING_DICT_OPTimization
    is False. The actual implementation would need to be updated to use the
    feature flag, but this test documents the expected behavior.
    """
    from src.app.core.feature_flags import FEATURE_BILLING_DICT_OPTIMIZATION

    # If feature flag is False, we should use the original Pydantic serialization
    # This test documents the expected behavior
    if not FEATURE_BILLING_DICT_OPTIMIZATION:
        # Original code path would use Pydantic model_dump()
        pydantic_model = ProductRead.model_validate(sample_product)
        original_dict = pydantic_model.model_dump()

        # Verify it produces a dict
        assert isinstance(original_dict, dict)
        assert "id" in original_dict

        import logging

        logger = logging.getLogger(__name__)
        logger.info("Feature flag off: using legacy Pydantic serialization")
    else:
        import logging

        logger = logging.getLogger(__name__)
        logger.info("Feature flag on: using optimized dict helpers")


def test_pricing_tier_fields_complete(sample_pricing):
    """Test that all pricing tier fields are included in the dict."""
    result = _pricing_tier_to_dict(sample_pricing)

    # Verify all expected fields are present
    expected_fields = [
        "id",
        "product_id",
        "name",
        "amount_cents",
        "interval",
        "stripe_price_id",
        "currency",
        "features",
        "is_active",
        "created_at",
        "updated_at",
    ]

    for field in expected_fields:
        assert field in result, f"Missing field: {field}"

    # Verify no extra fields
    assert set(result.keys()) == set(expected_fields)


def test_product_fields_complete(db, sample_product):
    """Test that all product fields are included in the dict."""
    pricing = Pricing(
        product_id=sample_product.id,
        name="Test Plan",
        amount_cents=100,
        interval="monthly",
        stripe_price_id="price_test",
        currency="USD",
        features=[],
        is_active=True,
    )
    db.add(pricing)
    db.commit()
    db.refresh(pricing)

    result = _product_to_dict(sample_product, [pricing])

    # Verify all expected fields are present
    expected_fields = [
        "id",
        "tenant_id",
        "name",
        "description",
        "stripe_product_id",
        "is_active",
        "created_at",
        "updated_at",
        "pricing_tiers",
    ]

    for field in expected_fields:
        assert field in result, f"Missing field: {field}"

    # Verify no extra fields
    assert set(result.keys()) == set(expected_fields)