"""FastAPI_saas-io15.4: the NARROWED blind except, and the boundary it protects.

``middleware/tenant.py`` held a blind ``except Exception`` around the
CRUDAdmin mount-path read. Unlike the other two muse-22 sites it is not a
deliberate boundary, and the reason is specific rather than general: that
value decides which paths skip tenant resolution, so a blanket swallow there
was a fail-OPEN on the tenant boundary. A fault nobody could see could
silently exempt a path that should have demanded ``X-Tenant-ID``.

Narrowing it changes behaviour in one way that must be pinned, not assumed:
the three settings faults still fall back to ``/admin``, but anything else now
PROPAGATES. Both halves are tested here, because a test that only checks the
fallback would pass just as happily against the original broad except.

The second half of this file is the counter-test the bead asks for: the
middleware must still produce its DESIGNED rejection when tenant resolution
fails. That behaviour lives outside the narrowed handler, so nothing in the
triage touched it — which is exactly why it needs a test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status


class TestAdminMountPrefixNarrowing:
    """The narrowed handler: specific faults fall back, the rest propagate."""

    def test_attribute_error_still_falls_back_to_admin(self) -> None:
        """A renamed/removed settings field is the case it was written for."""
        from src.app.middleware.tenant import _admin_mount_prefix

        class Settings:
            @property
            def admin(self):
                raise AttributeError("no such field")

        with patch("src.app.middleware.tenant.settings", Settings()):
            assert _admin_mount_prefix() == "/admin"

    def test_type_error_still_falls_back_to_admin(self) -> None:
        """A non-string mount path (.rstrip on a non-str) is the same case."""
        from src.app.middleware.tenant import _admin_mount_prefix

        class Settings:
            admin = MagicMock(CRUD_ADMIN_MOUNT_PATH=12345)

        with patch("src.app.middleware.tenant.settings", Settings()):
            assert _admin_mount_prefix() == "/admin"

    def test_value_error_still_falls_back_to_admin(self) -> None:
        """The ValueError arm is reachable and still falls back."""
        from src.app.middleware.tenant import _admin_mount_prefix

        class Admin:
            @property
            def CRUD_ADMIN_MOUNT_PATH(self):
                raise ValueError("mount path is not a string")

        class Settings:
            admin = Admin()

        with patch("src.app.middleware.tenant.settings", Settings()):
            assert _admin_mount_prefix() == "/admin"

    def test_unexpected_fault_now_propagates(self) -> None:
        """THE DEFINED BEHAVIOUR AFTER NARROWING: anything else is loud.

        Under the old ``except Exception`` this was swallowed and the admin
        prefix silently became ``/admin`` — which either over- or
        under-exempted tenant enforcement depending on the configured mount,
        with nothing in the logs. Failing at import is the safe outcome, and
        it is the point of the triage rather than a regression from it.

        This is the test that would NOT have passed before the change.
        """
        from src.app.middleware.tenant import _admin_mount_prefix

        class Admin:
            @property
            def CRUD_ADMIN_MOUNT_PATH(self):
                raise RuntimeError("config subsystem down")

        class Settings:
            admin = Admin()

        with patch("src.app.middleware.tenant.settings", Settings()):
            with pytest.raises(RuntimeError, match="config subsystem down"):
                _admin_mount_prefix()

    def test_the_real_setting_is_still_honoured(self) -> None:
        """Anti-over-fix: narrowing must not have broken the happy path."""
        from src.app.middleware.tenant import _admin_mount_prefix

        with patch("src.app.middleware.tenant.settings") as fake_settings:
            fake_settings.admin.CRUD_ADMIN_MOUNT_PATH = "/ops/panel/"
            assert _admin_mount_prefix() == "/ops/panel"


class TestTenantResolutionStillRejects:
    """COUNTER-TEST: the designed rejection survives the triage.

    The bead's concern is that narrowing a handler in the tenant middleware
    might have disturbed the rejection behaviour. These drive the middleware
    end to end and assert the actual status codes a client would see.
    """

    @staticmethod
    def _app():
        """A downstream ASGI app that records that it was reached."""
        reached = []

        async def app(scope, receive, send):
            reached.append(scope["path"])

        return app, reached

    @pytest.mark.asyncio
    async def test_missing_header_on_protected_path_is_rejected(self) -> None:
        """No X-Tenant-ID on a protected path -> 400, downstream not reached."""
        from src.app.middleware.tenant import TenantMiddleware

        app, reached = self._app()
        mw = TenantMiddleware(app)
        scope = {"type": "http", "method": "GET", "path": "/api/v1/protected", "headers": []}
        sent: list[int] = []

        async def send(message):
            if message["type"] == "http.response.start":
                sent.append(message["status"])

        await mw(scope, None, send)

        assert sent and sent[0] == status.HTTP_400_BAD_REQUEST, f"expected 400, got {sent}"
        assert reached == [], "the request reached the app despite a missing tenant header"

    @pytest.mark.asyncio
    async def test_unresolvable_slug_is_rejected(self) -> None:
        """An unknown slug on a protected path -> 403 (tenant_access_denied)."""
        from src.app.middleware.tenant import TenantMiddleware

        app, reached = self._app()
        mw = TenantMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/protected",
            "headers": [(b"x-tenant-id", b"ghost")],
        }
        sent: list[int] = []

        async def send(message):
            if message["type"] == "http.response.start":
                sent.append(message["status"])

        with patch.object(TenantMiddleware, "_get_tenant_by_slug", new=AsyncMock(return_value=None)):
            await mw(scope, None, send)

        assert sent and sent[0] == status.HTTP_403_FORBIDDEN, f"expected 403, got {sent}"
        assert reached == [], "the request reached the app with an unresolvable tenant"

    @pytest.mark.asyncio
    async def test_inactive_tenant_is_rejected(self) -> None:
        """A resolved but inactive tenant -> 403, downstream not reached."""
        from src.app.core.tenancy.context import TenantContext
        from src.app.middleware.tenant import TenantMiddleware

        app, reached = self._app()
        mw = TenantMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/protected",
            "headers": [(b"x-tenant-id", b"acme")],
        }
        sent: list[int] = []

        async def send(message):
            if message["type"] == "http.response.start":
                sent.append(message["status"])

        inactive = TenantContext(
            tenant_id=MagicMock(), tenant_slug="acme", tenant_name="Acme", is_active=False
        )
        with patch.object(TenantMiddleware, "_get_tenant_by_slug", new=AsyncMock(return_value=inactive)):
            await mw(scope, None, send)

        assert sent and sent[0] == status.HTTP_403_FORBIDDEN, f"expected 403, got {sent}"
        assert reached == [], "the request reached the app with an inactive tenant"

    @pytest.mark.asyncio
    async def test_exempt_path_proceeds_without_a_tenant(self) -> None:
        """The other half: an exempt path must still be ALLOWED to proceed.

        The exemption is the admin surface the narrowed handler feeds. If the
        triage had made the admin mount unreadable enough to 400, this is what
        would catch it.
        """
        from src.app.core.tenancy.context import NO_TENANT, get_current_tenant
        from src.app.middleware.tenant import ADMIN_EXEMPT_PREFIX, TenantMiddleware

        app, reached = self._app()
        mw = TenantMiddleware(app)
        path = f"{ADMIN_EXEMPT_PREFIX}/anything"
        scope = {"type": "http", "method": "GET", "path": path, "headers": []}
        sent: list[int] = []

        async def send(message):
            if message["type"] == "http.response.start":
                sent.append(message["status"])

        await mw(scope, None, send)

        assert reached == [path], f"the admin exemption did not apply; reached={reached}, sent={sent}"
        assert get_current_tenant() is NO_TENANT or get_current_tenant() is None
