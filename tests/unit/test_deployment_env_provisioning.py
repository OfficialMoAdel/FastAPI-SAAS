"""Provisioning smoke test for the untracked ``src/.env`` deployment artifact.

``src/.env`` is gitignored, so a clean ``git archive`` cannot contain it. The
application loads ``.env`` from the process working directory (and Docker mounts
the file at ``/code/.env``), not from ``src/.env`` directly. These tests prove
both halves of the operational contract:

- without a provisioned environment file, importing the app fails closed on the
  required database settings;
- with a provisioned ``.env`` in the working directory, the app imports cleanly.

They import only; no database or Redis connection is opened.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PROVISIONED_ENV = """\
ENVIRONMENT=local
SECRET_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ADMIN_PASSWORD=TestAdmin123!@#456
POSTGRES_USER=postgres
POSTGRES_PASSWORD=fresh-postgres-password
POSTGRES_DB=test_db
POSTGRES_SERVER=localhost
POSTGRES_PORT=5432
REDIS_CACHE_HOST=localhost
REDIS_CACHE_PORT=6379
REDIS_QUEUE_HOST=localhost
REDIS_QUEUE_PORT=6379
"""

_STRIPPED_EXACT = {
    "ADMIN_PASSWORD",
    "ENVIRONMENT",
    "SECRET_KEY",
    "PYTHONPATH",
}
_STRIPPED_PREFIXES = ("POSTGRES_", "REDIS_")


def _clean_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in _STRIPPED_EXACT and not key.upper().startswith(_STRIPPED_PREFIXES)
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _import_app(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import src.app.main; print('BOOT_OK')"],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_import_fails_closed_without_provisioned_environment(tmp_path):
    result = _import_app(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode != 0, "the app booted without its required database settings"
    assert "POSTGRES_USER" in output
    assert "POSTGRES_PASSWORD" in output


def test_import_succeeds_with_provisioned_env_in_working_directory(tmp_path):
    (tmp_path / ".env").write_text(PROVISIONED_ENV, encoding="utf-8")
    result = _import_app(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "BOOT_OK" in result.stdout
