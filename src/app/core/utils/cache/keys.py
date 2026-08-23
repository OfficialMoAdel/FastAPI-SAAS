"""Cache-key construction with optional tenant scoping."""

from uuid import UUID


def build_tenant_key(key: str, tenant_id: int | str | UUID | None = None) -> str:
    """Build a cache key, optionally prepending tenant isolation if present.

    Args:
        key: Base cache key
        tenant_id: Optional tenant identifier

    Returns:
        Tenant-scoped cache key or original key
    """
    if tenant_id is not None:
        if key.startswith(f"tenant:{tenant_id}:"):
            return key
        return f"tenant:{tenant_id}:{key}"
    return key


def tenant_cache_key(tenant_id: int | str, prefix: str, *key_parts: str | int) -> str:
    """Build a tenant-scoped cache key.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix (e.g., 'user', 'permissions')
        *key_parts: Additional key parts

    Returns
    -------
        Tenant-scoped cache key like 'tenant:123:user:456'

    Example:
        >>> tenant_cache_key(1, "user", 42)
        'tenant:1:user:42'
        >>> tenant_cache_key(1, "permissions", "admin")
        'tenant:1:permissions:admin'
    """
    parts = [f"tenant:{tenant_id}", prefix]
    parts.extend(str(p).replace(":", "_") for p in key_parts)
    return ":".join(parts)


def tenant_cache_pattern(tenant_id: int | str, prefix: str = "*") -> str:
    """Build a pattern for tenant-scoped cache keys.

    Args:
        tenant_id: Tenant identifier
        prefix: Cache key prefix pattern (default: '*' for all)

    Returns
    -------
        Pattern like 'tenant:123:*' for SCAN/KEYS operations

    Example:
        >>> tenant_cache_pattern(1)
        'tenant:1:*'
        >>> tenant_cache_pattern(1, "user")
        'tenant:1:user:*'
    """
    return f"tenant:{tenant_id}:{prefix}:*" if prefix != "*" else f"tenant:{tenant_id}:*"
