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

import hashlib
from functools import lru_cache
from typing import Final

from src.app.core.rbac.permissions import Permission as PermissionEnum

# Python handles arbitrarily large integers, and Redis can store large string representations.
# The previous 64-bit limit was an artificial ceiling since the bitmask is not stored in Postgres BIGINT.


def _build_permission_bit_map() -> dict[str, int]:
    """
    Build a mapping from permission slug to bit position.

    Each permission gets a unique bit (0-n).
    Sorted alphabetically for deterministic ordering across runs.
    """
    slugs = sorted(p.value for p in PermissionEnum)
    return {slug: i for i, slug in enumerate(slugs)}


def _compute_permission_version_hash() -> str:
    """
    Compute a hash of the sorted permission slugs.

    This hash is used to version Redis cache keys. If the set of permissions changes
    (e.g. adding a new permission), the alphabetical order and bit mapping changes.
    Using this hash in cache keys prevents bitmask collisions across deployments.
    """
    slugs = sorted(p.value for p in PermissionEnum)
    # Use SHA256 of the sorted slugs string representation
    return hashlib.sha256(",".join(slugs).encode()).hexdigest()[:8]


# Pre-computed at module load time
_PERMISSION_BIT_MAP: Final[dict[str, int]] = _build_permission_bit_map()
_BIT_PERMISSION_MAP: Final[dict[int, str]] = {v: k for k, v in _PERMISSION_BIT_MAP.items()}
_PERMISSION_VERSION_HASH: Final[str] = _compute_permission_version_hash()


class PermissionBitmask:
    """Utilities for bitmask-based permission operations."""

    @staticmethod
    def get_permission_version_hash() -> str:
        """Get the hash of current permission set for cache versioning."""
        return _PERMISSION_VERSION_HASH

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
            try:
                mask |= PermissionBitmask.get_bit(slug)
            except KeyError:
                continue
        return mask

    @staticmethod
    def decode(mask: int) -> set[str]:
        """
        Decode a bitmask back into a set of permission slugs.

        Uses Brian Kernighan's algorithm for O(k) complexity where k is
        the number of set bits (popcount), instead of O(P) where P is
        total permissions defined.

        Algorithm:
        1. (mask & -mask) isolates the lowest set bit
        2. .bit_length() - 1 gives the position (0-indexed)
        3. (mask & (mask - 1)) clears the lowest set bit
        4. Loop terminates when all bits are cleared

        Args:
            mask: Integer bitmask

        Returns:
            Set of permission slugs
        """
        permissions: set[str] = set()
        while mask:
            # Extract position of lowest set bit
            # (mask & -mask) isolates lowest set bit, bit_length() - 1 gives position
            pos = (mask & -mask).bit_length() - 1
            if pos in _BIT_PERMISSION_MAP:
                permissions.add(_BIT_PERMISSION_MAP[pos])
            # Clear lowest set bit (Kernighan's trick)
            mask &= mask - 1
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
        """Count the number of permissions in a bitmask using popcount."""
        return mask.bit_count()  # Python 3.10+ O(1) popcount


# Convenience exports
encode_permissions = PermissionBitmask.encode
decode_permissions = PermissionBitmask.decode
has_permission = PermissionBitmask.has_permission
