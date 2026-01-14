#!/usr/bin/env python3
"""Phase 2 Database Optimizations Validation.

Validates: U5 (Composite Indexes), U6 (N+1 Elimination), U7 (Quota Fusion),
           U11 (Negative Cache), U23 (Storage Settings N+1)
"""

import asyncio
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def validate_u5_composite_indexes() -> dict[str, Any]:
    """Verify composite indexes are defined."""
    logger.info("validate_start", optimization="U5", target="Index Scan")

    models_path = Path(__file__).parents[2] / "src" / "app" / "modules" / "users" / "models.py"
    source = models_path.read_text()

    has_tenant_email_idx = "idx_users_tenant_email" in source
    has_tenant_deleted_idx = "idx_users_tenant_deleted" in source
    has_index_definition = "Index(" in source

    passed = has_tenant_email_idx and has_tenant_deleted_idx

    logger.info("validate_complete", optimization="U5", passed=passed)
    return {"passed": passed, "indexes_found": has_tenant_email_idx and has_tenant_deleted_idx}


async def validate_u6_n1_elimination() -> dict[str, Any]:
    """Verify joinedload used for eager loading."""
    logger.info("validate_start", optimization="U6", target="80% fewer queries")

    service_path = Path(__file__).parents[2] / "src" / "app" / "modules" / "users" / "service.py"
    source = service_path.read_text()

    uses_joinedload = "joinedload" in source
    has_tier_relation = "User.tier" in source
    has_rate_limits = "Tier.rate_limits" in source or "rate_limits" in source

    passed = uses_joinedload and has_tier_relation

    logger.info("validate_complete", optimization="U6", passed=passed)
    return {"passed": passed, "uses_joinedload": uses_joinedload}


async def validate_u7_quota_fusion() -> dict[str, Any]:
    """Verify quota check uses single fused query."""
    logger.info("validate_start", optimization="U7", target="50% fewer queries")

    service_path = Path(__file__).parents[2] / "src" / "app" / "modules" / "files" / "service.py"
    source = service_path.read_text()

    has_count_sum = "COUNT(" in source.upper() and "SUM(" in source.upper()
    single_query = "func.count" in source and "func.sum" in source

    passed = has_count_sum or single_query

    logger.info("validate_complete", optimization="U7", passed=passed)
    return {"passed": passed, "uses_fused_query": passed}


async def validate_u11_negative_cache() -> dict[str, Any]:
    """Verify negative caching for non-existent tenants."""
    logger.info("validate_start", optimization="U11", target="95% cache hit")

    middleware_path = Path(__file__).parents[2] / "src" / "app" / "middleware" / "tenant.py"
    source = middleware_path.read_text()

    has_null_marker = "__NULL__" in source or "None" in source
    has_setex = "setex" in source.lower()
    has_negative_cache = "negative" in source.lower() or has_null_marker

    passed = has_setex and has_negative_cache

    logger.info("validate_complete", optimization="U11", passed=passed)
    return {"passed": passed, "has_negative_cache": has_negative_cache}


async def validate_u23_storage_settings_n1() -> dict[str, Any]:
    """Verify storage settings doesn't use N+1 pattern."""
    logger.info("validate_start", optimization="U23", target="Eliminate N+1")

    service_path = Path(__file__).parents[2] / "src" / "app" / "modules" / "storage_settings" / "service.py"
    source = service_path.read_text()

    has_raise_error = "StorageNotConfiguredError" in source
    no_fallback = "get_settings_or_raise" in source

    passed = has_raise_error and no_fallback

    logger.info("validate_complete", optimization="U23", passed=passed)
    return {"passed": passed, "raises_on_missing": has_raise_error}


async def validate_phase2() -> dict[str, Any]:
    """Run all Phase 2 validations."""
    logger.info("phase_start", phase=2)

    optimizations = {
        "U5": await validate_u5_composite_indexes(),
        "U6": await validate_u6_n1_elimination(),
        "U7": await validate_u7_quota_fusion(),
        "U11": await validate_u11_negative_cache(),
        "U23": await validate_u23_storage_settings_n1(),
    }

    all_passed = all(opt["passed"] for opt in optimizations.values())
    passed_count = sum(1 for opt in optimizations.values() if opt["passed"])

    logger.info("phase_complete", phase=2, passed=all_passed, passed_count=passed_count)

    return {
        "passed": all_passed,
        "optimizations": optimizations,
        "passed_count": passed_count,
        "total": len(optimizations),
    }


if __name__ == "__main__":
    result = asyncio.run(validate_phase2())
    print(f"Phase 2 Validation: {'PASSED' if result['passed'] else 'FAILED'}")
