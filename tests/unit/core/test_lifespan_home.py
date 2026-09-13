"""P3-#15: one lifespan home — phases live in core/setup.py, main.py stays thin."""

import inspect

import src.app.core.setup as core_setup
import src.app.main as main_module
from src.app.core.config import settings
from src.app.core.setup import apply_security_runtime_config, lifespan_with_admin


class TestLifespanHome:
    def test_main_reexports_setup_lifespan(self) -> None:
        # main.py is a thin composition root: the lifespan object it wires
        # into the app IS setup.py's — not a local copy.
        assert main_module.lifespan_with_admin is lifespan_with_admin
        assert main_module.lifespan_with_admin is core_setup.lifespan_with_admin

    def test_main_defines_no_lifespan_phases(self) -> None:
        source = inspect.getsource(main_module)
        for marker in ("def _init_", "def _warm_", "def _teardown_", "def _load_", "setattr(settings"):
            assert marker not in source, f"main.py should not contain {marker!r}"

    def test_phases_live_in_setup(self) -> None:
        source = inspect.getsource(core_setup)
        for name in (
            "_warm_token_blacklist_cache",
            "_init_pool_metrics",
            "_init_event_bus_and_queue",
            "_init_rate_limiting_and_security",
            "_load_security_runtime_config",
            "_warm_startup_caches",
            "_init_admin",
            "_teardown_security_listeners",
        ):
            assert f"def {name}" in source, f"{name} should live in setup.py"

    def test_lifespan_still_composes_factory(self) -> None:
        assert "lifespan_factory" in inspect.getsource(lifespan_with_admin)


class TestApplySecurityRuntimeConfig:
    def test_applies_and_coerces_known_keys(self) -> None:
        original_threshold = settings.security.AUTO_BAN_THRESHOLD
        original_enabled = settings.security.AUTO_BAN_ENABLED
        try:
            applied = apply_security_runtime_config(
                {"AUTO_BAN_THRESHOLD": "99", "AUTO_BAN_ENABLED": False}
            )
            assert applied == ["AUTO_BAN_ENABLED", "AUTO_BAN_THRESHOLD"]
            assert settings.security.AUTO_BAN_THRESHOLD == 99
            assert settings.security.AUTO_BAN_ENABLED is False
        finally:
            settings.security.AUTO_BAN_THRESHOLD = original_threshold
            settings.security.AUTO_BAN_ENABLED = original_enabled

    def test_skips_unknown_and_invalid_keys(self) -> None:
        applied = apply_security_runtime_config(
            {"NOT_A_REAL_KEY": "x", "AUTO_BAN_THRESHOLD": {"nested": "dict"}}
        )
        assert applied == []
