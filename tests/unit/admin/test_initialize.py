"""Unit tests for admin/initialize.py."""

from unittest.mock import AsyncMock, MagicMock, patch

from src.app.admin import initialize as admin_init
from src.app.core.config import EnvironmentOption


async def test_get_db_session_yields_session():
    fake_session = MagicMock()
    fake_cm = MagicMock()
    fake_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(admin_init, "get_db_context", return_value=fake_cm):
        async for session in admin_init._get_db_session():
            assert session is fake_session


def _patch_settings(enabled=True, redis_enabled=False, **overrides):
    settings = MagicMock()
    settings.CRUD_ADMIN_ENABLED = enabled
    settings.CRUD_ADMIN_REDIS_ENABLED = redis_enabled
    settings.CRUD_ADMIN_REDIS_HOST = "localhost"
    settings.CRUD_ADMIN_REDIS_PORT = 6379
    settings.CRUD_ADMIN_REDIS_DB = 0
    settings.CRUD_ADMIN_REDIS_PASSWORD = None
    settings.CRUD_ADMIN_MOUNT_PATH = "/admin"
    settings.CRUD_ADMIN_ALLOWED_IPS_LIST = []
    settings.CRUD_ADMIN_ALLOWED_NETWORKS_LIST = []
    settings.CRUD_ADMIN_MAX_SESSIONS = 5
    settings.CRUD_ADMIN_SESSION_TIMEOUT = 60
    settings.CRUD_ADMIN_TRACK_EVENTS = False
    settings.CRUD_ADMIN_TRACK_SESSIONS = False
    settings.SECRET_KEY = MagicMock()
    settings.SECRET_KEY.get_secret_value.return_value = "secret"
    settings.SESSION_SECURE_COOKIES = True
    settings.ENVIRONMENT = EnvironmentOption.LOCAL
    settings.ADMIN_USERNAME = "admin"
    settings.ADMIN_PASSWORD = MagicMock()
    settings.ADMIN_PASSWORD.get_secret_value.return_value = "pw"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_create_admin_disabled():
    settings = _patch_settings(enabled=False)
    with patch.object(admin_init, "settings", settings):
        result = admin_init.create_admin_interface()
    assert result is None


def test_create_admin_memory_backend():
    settings = _patch_settings(enabled=True, redis_enabled=False)
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views") as reg,
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        result = admin_init.create_admin_interface()
    assert result is not None
    CRUDAdmin.assert_called_once()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["session_backend"] == "memory"
    assert kwargs["redis_config"] is None
    reg.assert_called_once_with(result)


def test_create_admin_redis_backend_with_password():
    password = MagicMock()
    password.get_secret_value.return_value = "redispw"
    settings = _patch_settings(
        enabled=True,
        redis_enabled=True,
        CRUD_ADMIN_REDIS_PASSWORD=password,
    )
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views"),
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        admin_init.create_admin_interface()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["session_backend"] == "redis"
    assert kwargs["redis_config"]["password"] == "redispw"


def test_create_admin_redis_password_none_string():
    password = MagicMock()
    password.get_secret_value.return_value = "None"
    settings = _patch_settings(
        enabled=True,
        redis_enabled=True,
        CRUD_ADMIN_REDIS_PASSWORD=password,
    )
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views"),
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        admin_init.create_admin_interface()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["redis_config"]["password"] is None


def test_create_admin_no_initial_admin_when_username_empty():
    settings = _patch_settings(enabled=True, ADMIN_USERNAME=None)
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views"),
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        admin_init.create_admin_interface()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["initial_admin"] is None


def test_create_admin_no_initial_admin_when_password_empty():
    settings = _patch_settings(enabled=True)
    settings.ADMIN_PASSWORD.get_secret_value.return_value = ""
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views"),
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        admin_init.create_admin_interface()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["initial_admin"] is None


def test_create_admin_allowed_ips_none_when_empty():
    settings = _patch_settings(enabled=True, CRUD_ADMIN_ALLOWED_IPS_LIST=[])
    with (
        patch.object(admin_init, "settings", settings),
        patch.object(admin_init, "register_admin_views"),
        patch("src.app.admin.initialize.CRUDAdmin") as CRUDAdmin,
    ):
        admin_init.create_admin_interface()
    kwargs = CRUDAdmin.call_args.kwargs
    assert kwargs["allowed_ips"] is None
    assert kwargs["enforce_https"] is False
