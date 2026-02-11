import pytest
from unittest.mock import patch
from src.app.core.rbac.dependencies import _get_bitmask_cache_key
from src.app.core.rbac.bitmask import PermissionBitmask


def test_cache_key_versioning():
    """Test that RBAC cache keys include version hash."""
    user_id = 123
    tenant_id = 456

    key = _get_bitmask_cache_key(user_id, tenant_id)

    # Check format: perms:bitmask:{tenant_id}:{user_id}:{version}
    parts = key.split(":")
    assert len(parts) == 5
    assert parts[0] == "perms"
    assert parts[1] == "bitmask"
    assert parts[2] == str(tenant_id)
    assert parts[3] == str(user_id)

    # Verify version matches the one in bitmask module
    version = PermissionBitmask.get_permission_version_hash()
    assert parts[4] == version
    assert len(version) == 8  # We used hexdigest()[:8]


def test_version_hash_stability():
    """Test that version hash is stable across calls."""
    v1 = PermissionBitmask.get_permission_version_hash()
    v2 = PermissionBitmask.get_permission_version_hash()
    assert v1 == v2
