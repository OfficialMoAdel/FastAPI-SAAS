from uuid import UUID


def get_user_permissions_cache_key(user_id: int, tenant_id: int | UUID) -> str:
    """
    Generate the Redis cache key for a user's permissions within a specific tenant.

    Format: rbac:permissions:{user_id}:{tenant_id}
    """
    return f"rbac:permissions:{user_id}:{tenant_id}"


# Cache TTL for user permissions (5 minutes)
PERMISSIONS_CACHE_TTL = 300
