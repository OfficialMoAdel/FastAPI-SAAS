"""Candidate 05: lifecycle mechanism and application wiring have separate homes."""

import inspect

import src.app.composition as composition
import src.app.main as main_module
from src.app.composition import lifespan_with_admin
from src.app.core.config import settings
from src.app.core.security.runtime_config import apply_runtime_config


class TestLifespanHome:
    def test_main_reexports_composition_lifespan(self) -> None:
        # main.py is a thin composition root: the lifespan object it wires
        # into the app is the one assembled by the application composition root.
        assert main_module.lifespan_with_admin is lifespan_with_admin
        assert main_module.lifespan_with_admin is composition.lifespan_with_admin

    def test_main_defines_no_lifespan_phases(self) -> None:
        source = inspect.getsource(main_module)
        for marker in ("def _init_", "def _warm_", "def _teardown_", "def _load_", "setattr(settings"):
            assert marker not in source, f"main.py should not contain {marker!r}"

    def test_phases_live_in_composition(self) -> None:
        source = inspect.getsource(composition)
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


class TestMfaFernetWarm:
    """The boot warm-up must never be the reason the app fails to start."""

    def test_the_phase_uses_the_public_seam_not_the_private_getter(self) -> None:
        import asyncio
        from unittest.mock import patch

        from src.app.modules.auth.services.mfa import MFAService

        # The phase must hand the PUBLIC classmethod to the thread pool. A
        # private method reached across a module boundary (the old
        # `asyncio.to_thread(MFAService._get_fernet, MFAService()`) is exactly
        # the coupling `warm()` exists to remove. Driven through the phase
        # itself: the recorder sees the seam the phase actually calls, so a
        # route through any other entry point fails here.
        MFAService._fernet_instance = None
        with patch.object(MFAService, "warm", return_value=None) as warm:
            asyncio.run(composition._start_mfa_fernet_warm())
        warm.assert_called_once_with()

    def test_warm_does_not_raise_when_the_key_is_missing(self) -> None:
        from unittest.mock import patch

        from src.app.modules.auth.services.mfa import MFAService

        MFAService._fernet_instance = None
        with patch.object(settings.crypt, "ENCRYPTION_KEY", None):
            # A missing key is a request-path problem, not a boot problem: the
            # settings gate already enforces it for production/staging, and
            # refusing to boot locally over MFA takes the whole app down.
            MFAService.warm()

    def test_warm_populates_the_instance_when_the_key_is_present(self) -> None:
        from src.app.modules.auth.services.mfa import MFAService

        MFAService._fernet_instance = None
        MFAService.warm()
        assert MFAService._fernet_instance is not None

    def test_the_phase_survives_a_failed_derivation(self) -> None:
        import asyncio
        from unittest.mock import patch

        from src.app.modules.auth.services.mfa import MFAService

        MFAService._fernet_instance = None
        with patch.object(settings.crypt, "ENCRYPTION_KEY", None):
            # Boot continues: the phase is a best-effort warm-up, not a gate.
            asyncio.run(composition._start_mfa_fernet_warm())


class TestApplyRuntimeConfig:
    def test_applies_and_coerces_known_keys(self) -> None:
        original_threshold = settings.security.AUTO_BAN_THRESHOLD
        original_enabled = settings.security.AUTO_BAN_ENABLED
        try:
            applied = apply_runtime_config({"AUTO_BAN_THRESHOLD": "99", "AUTO_BAN_ENABLED": False})
            assert applied == ["AUTO_BAN_ENABLED", "AUTO_BAN_THRESHOLD"]
            assert settings.security.AUTO_BAN_THRESHOLD == 99
            assert settings.security.AUTO_BAN_ENABLED is False
        finally:
            settings.security.AUTO_BAN_THRESHOLD = original_threshold
            settings.security.AUTO_BAN_ENABLED = original_enabled

    def test_skips_unknown_and_invalid_keys(self) -> None:
        applied = apply_runtime_config({"NOT_A_REAL_KEY": "x", "AUTO_BAN_THRESHOLD": {"nested": "dict"}})
        assert applied == []
