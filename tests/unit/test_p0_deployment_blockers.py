import logging
from pathlib import Path

from src.app.modules import feature_flags

# Configure logging
logger = logging.getLogger(__name__)


class TestBrokenImportsFix:
    def setup_method(self, method):
        logger.info(f"Starting test: {method.__name__}")

    def teardown_method(self, method):
        logger.info(f"Finished test: {method.__name__}")

    def test_feature_flags_imports_successfully(self):
        """P0-2a: feature_flags module must import without error."""
        logger.info("Testing feature_flags import")
        assert feature_flags is not None
        # verify it's actually the module we expect
        assert feature_flags.__name__ == "src.app.modules.feature_flags"

    def test_notification_template_engine_imports_successfully(self):
        """P0-2b: the live email-template engine must import without error.

        S-M4: core/utils/templates.py (the near-dead duplicate with
        autoescape off) was deleted; notifications/service.py is the single
        template engine (SandboxedEnvironment + autoescape=True).
        """
        logger.info("Testing notifications template engine import")
        from src.app.modules import notifications

        assert notifications is not None
        assert notifications.__name__ == "src.app.modules.notifications"

    def test_rls_imports_successfully(self):
        """P0-2c: rls.py must import without error."""
        logger.info("Testing rls import")
        from src.app.core.db import rls

        assert rls is not None
        assert rls.__name__ == "src.app.core.db.rls"

    def test_no_session_module_references(self):
        """Verify no code references non-existent session module."""
        logger.info("Scanning codebase for broken references")
        # Use Python to search instead of grep (cross-platform)
        src_path = Path("src/app")
        broken_import = "from src.app.core.db.session"

        found_files = []
        for py_file in src_path.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                if broken_import in content:
                    found_files.append(str(py_file))
            except Exception:
                continue

        if found_files:
            logger.error(f"Found broken imports in: {found_files}")

        assert not found_files, f"Found broken imports in: {found_files}"
