"""
Unit tests for bitmask permission system.
"""

import pytest

from src.app.core.rbac.bitmask import (
    MAX_PERMISSIONS,
    PermissionBitmask,
    decode_permissions,
    encode_permissions,
    has_permission,
)
from src.app.core.rbac.permissions import Permission


class TestPermissionBitmaskGetBit:
    """Tests for PermissionBitmask.get_bit()"""

    def test_get_bit_returns_power_of_two(self) -> None:
        """Each permission should map to a unique power of 2."""
        bit = PermissionBitmask.get_bit(Permission.USERS_READ.value)
        # Should be 2^position
        assert bit > 0
        assert (bit & (bit - 1)) == 0  # Only one bit set

    def test_get_bit_different_permissions_different_bits(self) -> None:
        """Different permissions should have different bit values."""
        bit1 = PermissionBitmask.get_bit(Permission.USERS_READ.value)
        bit2 = PermissionBitmask.get_bit(Permission.USERS_MANAGE.value)
        assert bit1 != bit2

    def test_get_bit_unknown_permission_raises_keyerror(self) -> None:
        """Unknown permission slug should raise KeyError."""
        with pytest.raises(KeyError):
            PermissionBitmask.get_bit("unknown:permission")

    def test_get_bit_is_cached(self) -> None:
        """get_bit should be LRU cached for performance."""
        # Call multiple times
        for _ in range(10):
            PermissionBitmask.get_bit(Permission.USERS_READ.value)

        cache_info = PermissionBitmask.get_bit.cache_info()
        assert cache_info.hits >= 9  # First call is miss, rest are hits


class TestPermissionBitmaskEncode:
    """Tests for PermissionBitmask.encode()"""

    def test_encode_empty_set_returns_zero(self) -> None:
        """Empty permission set should encode to 0."""
        mask = encode_permissions(set())
        assert mask == 0

    def test_encode_single_permission(self) -> None:
        """Single permission should encode to its bit value."""
        mask = encode_permissions({Permission.USERS_READ.value})
        expected_bit = PermissionBitmask.get_bit(Permission.USERS_READ.value)
        assert mask == expected_bit

    def test_encode_multiple_permissions(self) -> None:
        """Multiple permissions should OR their bit values."""
        permissions = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        mask = encode_permissions(permissions)

        # Should have both bits set
        assert has_permission(mask, Permission.USERS_READ.value)
        assert has_permission(mask, Permission.ROLES_READ.value)
        assert not has_permission(mask, Permission.ROLES_MANAGE.value)

    def test_encode_all_permissions(self) -> None:
        """Encoding all permissions should set all bits."""
        all_slugs = {p.value for p in Permission}
        mask = encode_permissions(all_slugs)

        # All permissions should be present
        for perm in Permission:
            assert has_permission(mask, perm.value)


class TestPermissionBitmaskDecode:
    """Tests for PermissionBitmask.decode()"""

    def test_decode_zero_returns_empty_set(self) -> None:
        """Zero mask should decode to empty set."""
        permissions = decode_permissions(0)
        assert permissions == set()

    def test_decode_roundtrip(self) -> None:
        """Decode should be inverse of encode."""
        original = {
            Permission.USERS_READ.value,
            Permission.ROLES_MANAGE.value,
            Permission.FILES_READ.value,
        }
        mask = encode_permissions(original)
        decoded = decode_permissions(mask)
        assert decoded == original

    def test_decode_all_permissions(self) -> None:
        """Decoding all-1s mask should return all valid permissions."""
        all_slugs = {p.value for p in Permission}
        mask = encode_permissions(all_slugs)
        decoded = decode_permissions(mask)
        assert decoded == all_slugs


class TestPermissionBitmaskHasPermission:
    """Tests for PermissionBitmask.has_permission()"""

    def test_has_permission_true(self) -> None:
        """Should return True for permission in mask."""
        mask = encode_permissions({Permission.USERS_READ.value})
        assert has_permission(mask, Permission.USERS_READ.value) is True

    def test_has_permission_false(self) -> None:
        """Should return False for permission not in mask."""
        mask = encode_permissions({Permission.USERS_READ.value})
        assert has_permission(mask, Permission.USERS_MANAGE.value) is False

    def test_has_permission_zero_mask(self) -> None:
        """Zero mask should have no permissions."""
        assert has_permission(0, Permission.USERS_READ.value) is False

    def test_has_permission_unknown_slug_returns_false(self) -> None:
        """Unknown permission slug should return False, not raise."""
        mask = encode_permissions({Permission.USERS_READ.value})
        assert has_permission(mask, "unknown:permission") is False


class TestPermissionBitmaskHasAll:
    """Tests for PermissionBitmask.has_all_permissions()"""

    def test_has_all_permissions_true(self) -> None:
        """Should return True when all permissions present."""
        permissions = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        mask = encode_permissions(permissions)
        assert PermissionBitmask.has_all_permissions(mask, permissions) is True

    def test_has_all_permissions_false_missing_one(self) -> None:
        """Should return False when missing one permission."""
        mask = encode_permissions({Permission.USERS_READ.value})
        required = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        assert PermissionBitmask.has_all_permissions(mask, required) is False

    def test_has_all_permissions_superset_is_ok(self) -> None:
        """Should return True when mask has more than required."""
        mask = encode_permissions(
            {
                Permission.USERS_READ.value,
                Permission.USERS_MANAGE.value,
                Permission.ROLES_READ.value,
            }
        )
        required = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        assert PermissionBitmask.has_all_permissions(mask, required) is True


class TestPermissionBitmaskHasAny:
    """Tests for PermissionBitmask.has_any_permission()"""

    def test_has_any_permission_true(self) -> None:
        """Should return True when at least one permission present."""
        mask = encode_permissions({Permission.USERS_READ.value})
        check = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        assert PermissionBitmask.has_any_permission(mask, check) is True

    def test_has_any_permission_false(self) -> None:
        """Should return False when no permissions match."""
        mask = encode_permissions({Permission.USERS_READ.value})
        check = {Permission.ROLES_READ.value, Permission.ROLES_MANAGE.value}
        assert PermissionBitmask.has_any_permission(mask, check) is False


class TestPermissionBitmaskCombine:
    """Tests for PermissionBitmask.combine_masks()"""

    def test_combine_two_masks(self) -> None:
        """Combining masks should OR them together."""
        mask1 = encode_permissions({Permission.USERS_READ.value})
        mask2 = encode_permissions({Permission.ROLES_READ.value})
        combined = PermissionBitmask.combine_masks(mask1, mask2)

        assert has_permission(combined, Permission.USERS_READ.value)
        assert has_permission(combined, Permission.ROLES_READ.value)

    def test_combine_overlapping_masks(self) -> None:
        """Combining overlapping masks should not duplicate bits."""
        mask1 = encode_permissions({Permission.USERS_READ.value, Permission.ROLES_READ.value})
        mask2 = encode_permissions({Permission.ROLES_READ.value, Permission.FILES_READ.value})
        combined = PermissionBitmask.combine_masks(mask1, mask2)

        # Should have all three, not duplicates
        decoded = decode_permissions(combined)
        assert decoded == {
            Permission.USERS_READ.value,
            Permission.ROLES_READ.value,
            Permission.FILES_READ.value,
        }

    def test_combine_zero_masks(self) -> None:
        """Combining with zero should not change mask."""
        mask = encode_permissions({Permission.USERS_READ.value})
        combined = PermissionBitmask.combine_masks(mask, 0)
        assert combined == mask


class TestPermissionBitmaskUtilities:
    """Tests for utility methods."""

    def test_count_permissions(self) -> None:
        """count_permissions should return number of bits set."""
        permissions = {
            Permission.USERS_READ.value,
            Permission.ROLES_READ.value,
            Permission.FILES_READ.value,
        }
        mask = encode_permissions(permissions)
        assert PermissionBitmask.count_permissions(mask) == 3

    def test_count_permissions_zero(self) -> None:
        """Zero mask should have 0 permissions."""
        assert PermissionBitmask.count_permissions(0) == 0

    def test_get_all_permissions_returns_dict(self) -> None:
        """get_all_permissions should return mapping."""
        mapping = PermissionBitmask.get_all_permissions()
        assert isinstance(mapping, dict)
        assert len(mapping) == len(Permission)
        assert Permission.USERS_READ.value in mapping

    def test_max_permissions_not_exceeded(self) -> None:
        """Ensure we haven't exceeded 64 permissions."""
        assert len(Permission) <= MAX_PERMISSIONS


class TestPermissionBitmaskDeterminism:
    """Tests for deterministic bit assignment."""

    def test_bit_positions_are_deterministic(self) -> None:
        """Bit positions should be consistent across calls."""
        pos1 = PermissionBitmask.get_bit_position(Permission.USERS_READ.value)
        pos2 = PermissionBitmask.get_bit_position(Permission.USERS_READ.value)
        assert pos1 == pos2

    def test_encoding_is_deterministic(self) -> None:
        """Same input should always produce same output."""
        permissions = {Permission.USERS_READ.value, Permission.ROLES_READ.value}
        mask1 = encode_permissions(permissions)
        mask2 = encode_permissions(permissions)
        assert mask1 == mask2
