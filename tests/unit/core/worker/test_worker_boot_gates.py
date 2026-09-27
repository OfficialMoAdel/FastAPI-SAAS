"""The worker must gate its seams at boot, not on first use.

The web process gates ``ensure_tenant_model_registered`` and
``ensure_permission_graph_registered`` from ``composition.py``. The worker
did not — it relied on ``from src.app.models import *`` in
``core/worker/settings.py`` registering them as an import side effect.

That is a silent fallback for required config: delete that one import and
every worker job raises at REQUEST time, with no boot signal at all. The
gates are now called from worker ``startup()``, which runs before any job.

The ``import *`` is deliberately KEPT — it also feeds ``src.app.worker`` —
but it is no longer load-bearing for correctness.
"""

from unittest.mock import patch

import pytest

from src.app.core.worker import functions


class _FakeWorker:
    """ARQ hands startup a Worker; the gates raise before it is touched."""

    def __init__(self) -> None:
        self.redis = None
        self.ctx: dict = {}


def _stub_prelude() -> list:
    """Neutralise everything in startup() before the gates under test."""
    return [
        patch.object(functions, "assert_all_jobs_registered", return_value=[]),
        patch("src.app.core.worker.registry.ensure_feature_tasks_registered"),
        patch("src.app.core.worker.registry.ensure_feature_collaborators_registered"),
        patch("src.app.core.worker.audit.ensure_bypass_recorder_registered"),
    ]


class TestWorkerStartupGates:
    @pytest.mark.asyncio
    async def test_startup_invokes_every_gate(self) -> None:
        """The gates are CALLED by startup, not merely named in the file.

        Redis pools are disabled so startup runs to completion without a
        live backend; everything before the gates is stubbed.
        """
        called: list[str] = []

        with (
            patch.object(functions, "assert_all_jobs_registered", return_value=[]),
            patch("src.app.core.worker.registry.ensure_feature_tasks_registered"),
            patch("src.app.core.worker.registry.ensure_feature_collaborators_registered"),
            patch(
                "src.app.core.worker.audit.ensure_bypass_recorder_registered",
                lambda: called.append("bypass_recorder"),
            ),
            patch(
                "src.app.core.tenancy.models.ensure_tenant_model_registered",
                lambda: called.append("tenant_model"),
            ),
            patch(
                "src.app.core.rbac.models.ensure_permission_graph_registered",
                lambda: called.append("permission_graph"),
            ),
            patch("src.app.core.config.settings.redis_cache.REDIS_CACHE_ENABLED", False),
            patch("src.app.core.config.settings.redis_queue.REDIS_QUEUE_ENABLED", False),
        ):
            await functions.startup(_FakeWorker())

        assert "tenant_model" in called, f"startup did not gate the tenant model: {called}"
        assert "permission_graph" in called, f"startup did not gate the permission graph: {called}"
        assert "bypass_recorder" in called, f"startup did not gate the bypass recorder: {called}"

    @pytest.mark.asyncio
    async def test_worker_boot_actually_passes_the_gates(self) -> None:
        """The decisive test: a real worker boot must not falsely fail.

        Imports core/worker/settings.py (which performs the registration),
        then runs the gates exactly as startup does. If the star import ever
        stops registering these, this fails.
        """
        import src.app.core.worker.settings  # noqa: F401
        from src.app.core.rbac.models import ensure_permission_graph_registered
        from src.app.core.tenancy.models import ensure_tenant_model_registered

        ensure_tenant_model_registered()
        ensure_permission_graph_registered()

    def test_star_import_is_retained(self) -> None:
        """It also feeds src.app.worker, so it must not simply be deleted."""
        import src.app.core.worker.settings as ws

        assert hasattr(ws, "User") or hasattr(ws, "AuditLog"), (
            "core/worker/settings.py no longer registers the models; the "
            "star import is still required for src.app.worker and for the "
            "gates above"
        )

    def test_the_comment_no_longer_claims_correctness_depends_on_it(self) -> None:
        """The doc must not imply the import is the registration mechanism."""
        from pathlib import Path

        import src.app.core.worker.functions as fn

        text = Path(fn.__file__).read_text(encoding="utf-8")
        assert "no longer load-" in text or "load-\n    # bearing" in text or "load-bearing for correctness" in text, (
            "the worker startup comment should state that the import is no "
            "longer load-bearing for correctness"
        )


class TestUnregisteredSeamFailsAtBoot:
    def test_tenant_model_gate_raises_when_unregistered(self) -> None:
        from src.app.core.tenancy import models as tenancy_models

        original = tenancy_models._tenant_model
        try:
            tenancy_models._tenant_model = None
            with pytest.raises(RuntimeError, match="tenant model is not registered"):
                tenancy_models.ensure_tenant_model_registered()
        finally:
            tenancy_models._tenant_model = original

    def test_permission_graph_gate_raises_when_unregistered(self) -> None:
        from src.app.core.rbac import models as rbac_models

        original = rbac_models._permission_graph
        try:
            rbac_models._permission_graph = None
            with pytest.raises(RuntimeError, match="permission graph is not registered"):
                rbac_models.ensure_permission_graph_registered()
        finally:
            rbac_models._permission_graph = original
