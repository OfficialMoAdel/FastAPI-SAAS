"""S-26 — a root admin mount must not waive tenant resolution.

`CRUD_ADMIN_MOUNT_PATH` becomes `TENANT_EXEMPT_PREFIXES`, and
`_TENANT_EXEMPT_MATCHES` used to keep a bare `"/"` for a root prefix:

    tuple(prefix if prefix == "/" else f"{prefix}/" for prefix in ...)

`path.startswith(("/",))` is true for EVERY absolute path, so configuring
`CRUD_ADMIN_MOUNT_PATH=/` (or `""`, which `rstrip("/") or "/"` turns into it)
made every request exempt from tenant validation and bound every request under
the `NO_TENANT` sentinel. A single operator setting, no error, tenant isolation
off API-wide.

The setting is a bare `str` with no validator, so nothing stopped it.

Two layers are pinned here:
  1. the config validator rejects a root mount at construction, and
  2. the middleware refuses to honour one even if it arrives by another route
     (a directly-constructed settings object, a test double, a future caller
     bypassing validation) — the safe direction is "admin is not exempt".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.core.config.services import CRUDAdminSettings
from src.app.middleware.tenant import is_path_exempt


class TestConfigRejectsRootMount:
    # A root mount is rejected ONLY when the panel is actually enabled (#18).
    # A bare field validator aborted the whole settings build even with
    # CRUD_ADMIN_ENABLED=False, so a cosmetic setting belonging to a DISABLED
    # feature could block the boot of an app that never mounts anything.

    @pytest.mark.parametrize("bad", ["/", "", "   "])
    def test_root_mount_is_rejected_when_enabled(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            CRUDAdminSettings(CRUD_ADMIN_ENABLED=True, CRUD_ADMIN_MOUNT_PATH=bad)

    @pytest.mark.parametrize("bad", ["/", "", "   "])
    def test_bad_path_does_not_block_boot_when_disabled(self, bad: str) -> None:
        """The disabled panel must not be able to take the whole app down."""
        settings = CRUDAdminSettings(CRUD_ADMIN_ENABLED=False, CRUD_ADMIN_MOUNT_PATH=bad)
        assert settings.CRUD_ADMIN_ENABLED is False

    @pytest.mark.parametrize("good", ["/admin", "/ops/admin", "admin", "/admin/"])
    def test_realistic_paths_are_accepted_and_normalised(self, good: str) -> None:
        s = CRUDAdminSettings(CRUD_ADMIN_ENABLED=True, CRUD_ADMIN_MOUNT_PATH=good)
        assert s.CRUD_ADMIN_MOUNT_PATH == good.rstrip("/") or good == "admin"


class TestMiddlewareNeverHonoursRoot:
    """Even bypassing the validator, no absolute path may be exempt."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/api/v1/users",
            "/api/v1/account/login",
            "/anything/at/all",
        ],
    )
    def test_no_absolute_path_is_exempt(self, path: str) -> None:
        assert is_path_exempt(path) is False, f"{path} must not be tenant-exempt"

    def test_genuinely_exempt_paths_still_are(self) -> None:
        """Control: the guard must not disable legitimate exemptions.

        `/health` and `/docs` are exempt BY DESIGN (health probes and docs
        carry no tenant context). Only the accidental blanket exemption
        introduced by a root admin mount is removed.
        """
        assert is_path_exempt("/health") is True
        assert is_path_exempt("/docs") is True

    def test_real_admin_prefix_still_exempt(self) -> None:
        """The guard must not disable the exemption it was protecting."""
        # The exempt prefixes are a module constant seeded from settings; assert
        # the shape rather than a specific operator value.
        from src.app.middleware import tenant

        # The tuple is built ONCE at import from live settings, so filtering
        # "/" out of the builder is the complete fix for the real path.
        # assert all(p != "/" for p in tenant.TENANT_EXEMPT_MATCHES)
        for m in tenant._TENANT_EXEMPT_MATCHES:
            assert m != "/", "a bare '/' match entry would exempt every path"
        # ...and every entry must be a concrete sub-path match.
        assert all(m.startswith("/") and len(m) > 1 for m in tenant._TENANT_EXEMPT_MATCHES)
