"""S-36 — the ARQ worker must apply the same log redaction as the web process.

The worker never called `setup_logging()`, so structlog ran with its default
processor chain: no `SanitizingProcessor`, i.e. no redaction of tokens,
emails or DSNs — in the exact process whose job logs carry them.

The subtle part is ORDERING. Every handler module binds
`logger = get_logger(__name__)` at import time, and `setup_logging()` sets
`cache_logger_on_first_use=True`. Configuring logging from the worker's
`startup()` hook — which is where it was first added — is therefore TOO LATE:
those loggers are already bound to the unsanitised chain. The call must
precede the handler imports, i.e. sit at the top of `src/app/worker.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORKER = Path("src/app/worker.py")

# Runs in a fresh interpreter: loads .env (a bare subprocess gets no conftest),
# imports the worker module, then reports whether the sanitizer is installed.
PROBE = (
    "from dotenv import load_dotenv; load_dotenv('src/.env');"
    "import src.app.worker;"
    "import structlog;"
    "from src.app.core.observability.log_sanitizer import SanitizingProcessor;"
    "procs = structlog.get_config()['processors'];"
    "print('SANITIZER_INSTALLED' if any("
    "isinstance(p, SanitizingProcessor) for p in procs) else 'SANITIZER_MISSING')"
)


def _source() -> str:
    return WORKER.read_text(encoding="utf-8")


class TestSetupLoggingPrecedesHandlerImports:
    def test_setup_logging_is_called_at_module_import(self) -> None:
        assert "setup_logging()" in _source(), "the worker module must configure logging on import"

    def test_it_is_called_before_the_handler_imports(self) -> None:
        """The whole fix: configuration must precede `get_logger()` binding."""
        src = _source()
        call = src.index("setup_logging()")
        first_handler = src.index("from src.app.modules.audit_logs.service import")
        assert call < first_handler, (
            "setup_logging() must run BEFORE handler modules are imported, "
            "otherwise their module-level get_logger() calls bind to the "
            "unsanitised default processor chain"
        )

    def test_startup_hook_does_not_duplicate_the_call(self) -> None:
        """The late hook was the bug; it must not reappear."""
        from src.app.core.worker import functions

        assert "setup_logging" not in functions.startup.__code__.co_names, (
            "setup_logging in startup() is too late — cache_logger_on_first_use "
            "has already bound the handler loggers"
        )


class TestRedactionActuallyAppliesInAWorkerProcess:
    def test_worker_import_installs_the_sanitizer(self) -> None:
        """End-to-end: a fresh worker process must end up sanitized.

        This is the test that distinguishes "I called the function" from
        "redaction is in effect".
        """
        proc = subprocess.run(
            [sys.executable, "-c", PROBE],
            env={**os.environ},
            capture_output=True,
            text=True,
            cwd=str(Path.cwd()),
            timeout=300,
        )
        detail = "stdout=" + proc.stdout[-500:] + " stderr=" + proc.stderr[-800:]
        assert proc.returncode == 0, f"probe process failed: {detail}"
        assert "SANITIZER_INSTALLED" in proc.stdout, f"worker process is unsanitized: {detail}"
