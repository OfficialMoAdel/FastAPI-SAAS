"""Tests for the framework-independent ownership policy."""

import pytest

from src.app.core.security.ownership import owns_resource, require_resource_ownership
from src.app.shared.exceptions import ForbiddenError


def test_owner_ids_compare_across_string_representations() -> None:
    assert owns_resource(7, "7")
    assert not owns_resource(7, "8")


def test_superuser_bypasses_ownership_check() -> None:
    require_resource_ownership(7, 8, is_superuser=True)


def test_mismatched_owner_is_forbidden() -> None:
    with pytest.raises(ForbiddenError, match="permission to access"):
        require_resource_ownership(7, 8)
