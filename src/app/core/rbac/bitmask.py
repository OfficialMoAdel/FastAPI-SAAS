"""
Bitmask-based permission system for O(1) permission checks.

This module provides utilities for encoding permissions as bitmasks,
enabling constant-time permission checks via bitwise operations.

Benefits over Redis Sets:
- Single integer in Redis vs set of strings (lower memory)
- Bitwise AND for permission check: (user_mask & permission_bit) != 0
- Combine multiple role masks with OR: role1_mask | role2_mask
- Check multiple permissions at once with AND: (user_mask & required_mask) == required_mask

Usage:
    from src.app.core.rbac.bitmask import PermissionBitmask

    # Encode permissions to mask
    mask = PermissionBitmask.encode({"users:read", "users:manage"})

    # Check single permission
    if PermissionBitmask.has_permission(mask, "users:read"):
        ...

    # Check multiple permissions (all required)
    if PermissionBitmask.has_all_permissions(mask, {"users:read", "roles:read"}):
        ...
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from .permissions import Permission as PermissionEnum

# Maximum 64 permissions for single BigInt (PostgreSQL BIGINT is 64-bit signed)
# For more permissions, use array of BigInts or switch to BYTEA
MAX_PERMISSIONS: Final[int] = 64


def _build_permission_bit_map() -> dict[str, int]:
    """
    Build a mapping from permission slug to bit position.

    Each permission gets a unique bit (0-63).
    Sorted alphabetically for deterministic ordering across runs.
    """
    slugs = sorted(p.value for p in PermissionEnum)
    if len(slugs) > MAX_PERMISSIONS:
        raise ValueError(f"Too many permissions ({len(slugs)}). Max supported: {MAX_PERMISSIONS}")
    return {slug: i for i, slug in enumerate(slugs)}


# Pre-computed at module load time
_PERMISSION_BIT_MAP: Final[dict[str, int]] = _build_permission_bit_map()
_BIT_PERMISSION_MAP: Final[dict[int, str]] = {v: k for k, v in _PERMISSION_BIT_MAP.items()}


class PermissionBitmask:
    """Utilities for bitmask-based permission operations."""

    @staticmethod
    @lru_cache(maxsize=256)
    def get_bit(permission_slug: str) -> int:
        """
        Get the bit value for a permission slug.

        Args:
            permission_slug: The permission slug (e.g., "users:read")

        Returns:
            Bit value (2^position) for the permission

        Raises:
            KeyError: If permission slug is not registered
        """
        position = _PERMISSION_BIT_MAP[permission_slug]
        return 1 << position

    @staticmethod
    def get_bit_position(permission_slug: str) -> int:
        """Get the bit position (0-63) for a permission slug."""
        return _PERMISSION_BIT_MAP[permission_slug]

    @staticmethod
    def encode(permission_slugs: set[str]) -> int:
        """
        Encode a set of permission slugs into a bitmask.

        Args:
            permission_slugs: Set of permission slugs

        Returns:
            Integer bitmask with bits set for each permission
        """
        mask = 0
        for slug in permission_slugs:
            mask |= PermissionBitmask.get_bit(slug)
        return mask

    @staticmethod
    def decode(mask: int) -> set[str]:
        """
        Decode a bitmask back into a set of permission slugs.

        Args:
            mask: Integer bitmask

        Returns:
            Set of permission slugs
        """
        permissions: set[str] = set()
        for position, slug in _BIT_PERMISSION_MAP.items():
            if mask & (1 << position):
                permissions.add(slug)
        return permissions

    @staticmethod
    def has_permission(mask: int, permission_slug: str) -> bool:
        """
        Check if a bitmask contains a specific permission.

        O(1) bitwise check: (mask & permission_bit) != 0

        Args:
            mask: User's permission bitmask
            permission_slug: Permission to check

        Returns:
            True if permission is set in mask
        """
        try:
            bit = PermissionBitmask.get_bit(permission_slug)
            return (mask & bit) != 0
        except KeyError:
            return False

    @staticmethod
    def has_all_permissions(mask: int, permission_slugs: set[str]) -> bool:
        """
        Check if a bitmask contains ALL specified permissions.

        Args:
            mask: User's permission bitmask
            permission_slugs: Set of permissions to check

        Returns:
            True if all permissions are set in mask
        """
        required_mask = PermissionBitmask.encode(permission_slugs)
        return (mask & required_mask) == required_mask

    @staticmethod
    def has_any_permission(mask: int, permission_slugs: set[str]) -> bool:
        """
        Check if a bitmask contains ANY of the specified permissions.

        Args:
            mask: User's permission bitmask
            permission_slugs: Set of permissions to check

        Returns:
            True if at least one permission is set in mask
        """
        required_mask = PermissionBitmask.encode(permission_slugs)
        return (mask & required_mask) != 0

    @staticmethod
    def combine_masks(*masks: int) -> int:
        """
        Combine multiple permission masks using bitwise OR.

        Useful for computing a user's total permissions from multiple roles.

        Args:
            *masks: Variable number of bitmasks

        Returns:
            Combined bitmask with all permissions
        """
        result = 0
        for mask in masks:
            result |= mask
        return result

    @staticmethod
    def get_all_permissions() -> dict[str, int]:
        """
        Get mapping of all permission slugs to their bit positions.

        Returns:
            Dict mapping slug -> bit position (0-63)
        """
        return dict(_PERMISSION_BIT_MAP)

    @staticmethod
    def count_permissions(mask: int) -> int:
        """Count the number of permissions in a bitmask."""
        return bin(mask).count("1")


# Convenience exports
encode_permissions = PermissionBitmask.encode
decode_permissions = PermissionBitmask.decode
has_permission = PermissionBitmask.has_permission
