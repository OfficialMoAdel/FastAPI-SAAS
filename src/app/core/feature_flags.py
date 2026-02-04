"""Feature flags for gradual rollout of optimizations.

Feature flags allow controlled rollout and easy rollback of optimizations.
All flags default to True (enabled) for production use.
Set to False via environment variables to disable if issues arise.

Usage:
    from src.app.core.feature_flags import FEATURE_BILLING_DICT_OPTIMIZATION

    if FEATURE_BILLING_DICT_OPTIMIZATION:
        # Use optimized code path
    else:
        # Use original code path
"""

import os

# Billing module optimization flags
FEATURE_BILLING_DICT_OPTIMIZATION = (
    os.getenv(
        "FEATURE_BILLING_DICT_OPTIMIZATION",
        "true",
    ).lower()
    == "true"
)

# Tenant module optimization flags
FEATURE_TENANT_DICT_OPTIMIZATION = (
    os.getenv(
        "FEATURE_TENANT_DICT_OPTIMIZATION",
        "true",
    ).lower()
    == "true"
)

# RBAC module optimization flags
FEATURE_RBAC_CACHE_ENABLED = (
    os.getenv(
        "FEATURE_RBAC_CACHE_ENABLED",
        "true",
    ).lower()
    == "true"
)

# Session module optimization flags
FEATURE_SESSION_PIPELINE_EVICTION = (
    os.getenv(
        "FEATURE_SESSION_PIPELINE_EVICTION",
        "true",
    ).lower()
    == "true"
)

# Event bus optimization flags
FEATURE_EVENT_BUS_LUA_IDEMPOTENCY = (
    os.getenv(
        "FEATURE_EVENT_BUS_LUA_IDEMPOTENCY",
        "true",
    ).lower()
    == "true"
)

FEATURE_EVENT_BUS_BATCH_ENQUEUE = (
    os.getenv(
        "FEATURE_EVENT_BUS_BATCH_ENQUEUE",
        "true",
    ).lower()
    == "true"
)

# Passkey optimization flags
FEATURE_PASSKEY_COLUMN_SELECT = (
    os.getenv(
        "FEATURE_PASSKEY_COLUMN_SELECT",
        "true",
    ).lower()
    == "true"
)

# DLQ optimization flags
FEATURE_DLQ_BOUNDED_SIZE = (
    os.getenv(
        "FEATURE_DLQ_BOUNDED_SIZE",
        "true",
    ).lower()
    == "true"
)

# XFetch optimization flags
FEATURE_XFETCH_LUA_SCRIPT = (
    os.getenv(
        "FEATURE_XFETCH_LUA_SCRIPT",
        "true",
    ).lower()
    == "true"
)


def get_all_flags() -> dict[str, bool]:
    """Get all feature flags and their current values.

    Returns:
        Dictionary mapping feature flag names to boolean values.
    """
    return {
        "FEATURE_BILLING_DICT_OPTIMIZATION": FEATURE_BILLING_DICT_OPTIMIZATION,
        "FEATURE_TENANT_DICT_OPTIMIZATION": FEATURE_TENANT_DICT_OPTIMIZATION,
        "FEATURE_RBAC_CACHE_ENABLED": FEATURE_RBAC_CACHE_ENABLED,
        "FEATURE_SESSION_PIPELINE_EVICTION": FEATURE_SESSION_PIPELINE_EVICTION,
        "FEATURE_EVENT_BUS_LUA_IDEMPOTENCY": FEATURE_EVENT_BUS_LUA_IDEMPOTENCY,
        "FEATURE_EVENT_BUS_BATCH_ENQUEUE": FEATURE_EVENT_BUS_BATCH_ENQUEUE,
        "FEATURE_PASSKEY_COLUMN_SELECT": FEATURE_PASSKEY_COLUMN_SELECT,
        "FEATURE_DLQ_BOUNDED_SIZE": FEATURE_DLQ_BOUNDED_SIZE,
        "FEATURE_XFETCH_LUA_SCRIPT": FEATURE_XFETCH_LUA_SCRIPT,
    }
