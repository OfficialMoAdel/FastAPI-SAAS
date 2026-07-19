"""Tests for the ARQ worker entrypoint.

Covers src/app/core/worker/main.py (currently 0% coverage).
"""

import sys
from unittest import mock

from src.app.core.worker import main as worker_main_module


class TestWorkerMain:
    """Verify the worker main() bootstraps the ARQ CLI correctly."""

    def test_main_appends_default_settings_when_no_args(self) -> None:
        """With no CLI args, main injects the WorkerSettings path."""
        original_argv = sys.argv
        sys.argv = ["arq"]
        try:
            with mock.patch.object(worker_main_module, "cli") as mock_cli:
                with mock.patch.object(worker_main_module, "asyncio") as mock_asyncio:
                    # Force the win32 branch so uvloop import is skipped (portable)
                    with mock.patch.object(worker_main_module, "sys") as mock_sys:
                        mock_sys.platform = "win32"
                        mock_sys.argv = ["arq"]
                        worker_main_module.main()
                        # default settings path appended
                        assert mock_sys.argv == [
                            "arq",
                            "src.app.core.worker.settings.WorkerSettings",
                        ]
                        mock_asyncio.set_event_loop_policy.assert_not_called()
                        mock_cli.assert_called_once()
        finally:
            sys.argv = original_argv

    def test_main_sets_uvloop_policy_on_non_win32(self) -> None:
        """On non-win32, uvloop event loop policy is configured when available."""
        original_argv = sys.argv
        sys.argv = ["arq"]
        try:
            with mock.patch.object(worker_main_module, "cli") as mock_cli:
                with mock.patch.object(worker_main_module, "asyncio") as mock_asyncio:
                    with mock.patch.object(worker_main_module, "sys") as mock_sys:
                        mock_sys.platform = "linux"
                        mock_sys.argv = ["arq"]
                        fake_uvloop = mock.MagicMock()
                        with mock.patch.dict("sys.modules", {"uvloop": fake_uvloop}):
                            worker_main_module.main()
                            mock_asyncio.set_event_loop_policy.assert_called_once_with(
                                fake_uvloop.EventLoopPolicy.return_value
                            )
                        assert mock_sys.argv == [
                            "arq",
                            "src.app.core.worker.settings.WorkerSettings",
                        ]
                        mock_cli.assert_called_once()
        finally:
            sys.argv = original_argv

    def test_main_falls_back_when_uvloop_missing(self) -> None:
        """When uvloop is not installed, falls back to default asyncio policy."""
        original_argv = sys.argv
        sys.argv = ["arq"]
        try:
            with mock.patch.object(worker_main_module, "cli") as mock_cli:
                with mock.patch.object(worker_main_module, "asyncio") as mock_asyncio:
                    with mock.patch.object(worker_main_module, "sys") as mock_sys:
                        mock_sys.platform = "linux"
                        mock_sys.argv = ["arq"]
                        # uvloop absent from sys.modules -> ImportError path
                        with mock.patch.dict("sys.modules", {"uvloop": None}):
                            worker_main_module.main()
                            mock_asyncio.set_event_loop_policy.assert_not_called()
                        mock_cli.assert_called_once()
        finally:
            sys.argv = original_argv

    def test_main_uses_explicit_args(self) -> None:
        """When args are provided, main does not override them."""
        original_argv = sys.argv
        sys.argv = ["arq", "custom.module:Settings"]
        try:
            with mock.patch.object(worker_main_module, "cli") as mock_cli:
                with mock.patch.object(worker_main_module, "sys") as mock_sys:
                    mock_sys.platform = "win32"
                    mock_sys.argv = ["arq", "custom.module:Settings"]
                    worker_main_module.main()
                    assert mock_sys.argv == ["arq", "custom.module:Settings"]
                    mock_cli.assert_called_once()
        finally:
            sys.argv = original_argv
