"""S-21: the admin panel must actually be MOUNTED and reachable.

#11: the target application is passed to `_init_admin` rather than read from a
module global. A global is overwritten by the LAST `create_application` call,
so a process that builds more than one app mounted the panel onto whichever was
built last rather than the one whose lifespan was running.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.routing import Mount


@pytest.mark.asyncio
async def test_admin_is_mounted_on_the_application():
    """initialize() runs, then admin.app is mounted at get_url_prefix()."""
    from src.app import composition as comp
    from src.app.admin.initialize import create_admin_interface

    app = FastAPI()

    fake_admin = MagicMock()
    fake_admin.initialize = AsyncMock()
    fake_admin.get_url_prefix.return_value = "/admin"
    fake_admin.app = FastAPI()

    @fake_admin.app.get("/login")
    async def _login():
        return {"ok": True}

    with (
        patch.object(comp, "create_admin_interface", create_admin_interface, create=True),
        patch("src.app.admin.initialize.create_admin_interface", return_value=fake_admin),
        patch.object(comp.settings.admin, "CRUD_ADMIN_ENABLED", True),
    ):
        # The app is now passed IN (#11). It used to be a module-level
        # `_application` global, which the last `create_application` call would
        # overwrite — so a suite building two apps mounted the panel onto the
        # wrong one. Binding it per-lifespan removes that class entirely.
        #
        # The panel instance itself lives on `app.state.admin` (F5), for the
        # same reason: a module global would be overwritten by the last
        # `create_application` call.
        await comp._init_admin(app)

    mounts = [r for r in app.routes if isinstance(r, Mount)]
    assert mounts, f"admin panel was NOT mounted; routes={[getattr(r,'path','?') for r in app.routes]}"
    assert mounts[0].path == "/admin", mounts[0].path

    # httpx ASGITransport rather than `with TestClient(app)`: the context-managed
    # TestClient spins a blocking anyio portal and tears down an event loop of
    # its own. On Windows that adds loop churn the suite does not need, and the
    # repo's own 300s hang guard is aimed at exactly that class of leak. This
    # reuses the loop the async test is already running in.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/admin/login")

    assert r.status_code == 200, f"/admin/login -> {r.status_code}"
    fake_admin.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_root_mount_is_refused():
    """S-26 guard: mounting at '/' would waive tenant isolation."""
    from src.app import composition as comp

    app = FastAPI()
    fake_admin = MagicMock()
    fake_admin.initialize = AsyncMock()
    fake_admin.get_url_prefix.return_value = "/"
    fake_admin.app = FastAPI()

    with (
        patch("src.app.admin.initialize.create_admin_interface", return_value=fake_admin),
    ):
        await comp._init_admin(app)

    assert not [r for r in app.routes if isinstance(r, Mount)]
    assert app.state.admin is fake_admin


@pytest.mark.asyncio
async def test_admin_instance_is_per_application():
    """F5: two applications must not share one panel instance.

    The panel used to live in a module global, so the second app to boot
    reused the first app's already-initialized panel — and, worse, the
    `if admin is None` guard meant the second app never got its own
    `create_admin_interface()` call at all.
    """
    from src.app import composition as comp

    first = FastAPI()
    second = FastAPI()

    def _make_fake(url_prefix: str) -> MagicMock:
        fake = MagicMock()
        fake.initialize = AsyncMock()
        fake.get_url_prefix.return_value = url_prefix
        fake.app = FastAPI()
        return fake

    first_admin = _make_fake("/admin")
    second_admin = _make_fake("/other-admin")

    with patch(
        "src.app.admin.initialize.create_admin_interface", side_effect=[first_admin, second_admin]
    ):
        await comp._init_admin(first)
        await comp._init_admin(second)

    assert first.state.admin is first_admin
    assert second.state.admin is second_admin
    first_admin.initialize.assert_awaited_once()
    second_admin.initialize.assert_awaited_once()
