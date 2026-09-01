"""Worker job-registration contract tests (candidate 10, B8 bug class).

The B8 bug: ``cleanup_leaked_webhook_lock`` was ENQUEUED by the web process
but not DEFINED/registered on the ARQ worker — a job name missing in one of
the two places. ``assert_all_jobs_registered`` compares the static set of
runtime-enqueued job names against WorkerSettings.functions; wired into
worker startup as a hard failure so the miss can never ship silently again.
"""

import inspect

from src.app.core.worker.functions import ENQUEUED_JOB_NAMES, assert_all_jobs_registered


def test_all_runtime_enqueued_jobs_registered():
    """Health check: every ENQUEUED_JOB_NAME appears in WorkerSettings.functions."""
    missing = assert_all_jobs_registered()
    assert missing == [], f"Jobs missing worker registration: {missing}"


def test_assertion_detects_missing_registration():
    """The guard itself works: a name absent from the registry is reported."""
    functions = {"deliver_webhook_task": object()}  # missing the rest
    missing = assert_all_jobs_registered(functions)
    assert "send_email_task" in missing
    assert "cleanup_leaked_webhook_lock" in missing
    assert "process_event_handler" in missing


def test_assertion_passes_when_all_registered():
    functions = {name: object() for name in ENQUEUED_JOB_NAMES}
    assert assert_all_jobs_registered(functions) == []


def test_startup_wires_the_assertion():
    """worker.startup must call the guard — the B8 class must fail startup."""

    from src.app.core.worker import functions as worker_functions

    source = inspect.getsource(worker_functions.startup)
    assert "assert_all_jobs_registered" in source


def test_enqueued_names_cover_the_known_enqueue_sites():
    """Every literal job name enqueued in src/app is in the contract set.

    A new ``enqueue_job("some_new_task", ...)`` without a matching
    registration is the B8 bug — this test forces the author to add the
    name to ENQUEUED_JOB_NAMES (and to the worker registry, which
    test_all_runtime_enqueued_jobs_registered then enforces).
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        [sys.executable, "-c", (
            "import re, pathlib\n"
            f"root = pathlib.Path(r'{repo}') / 'src' / 'app'\n"
            "names = set()\n"
            "pat = re.compile(r'enqueue_job\\(\\s*\\\"([a-z_]+)\\\"')\n"
            "for p in root.rglob('*.py'):\n"
            "    if '__pycache__' in str(p):\n"
            "        continue\n"
            "    try:\n"
            "        names |= set(pat.findall(p.read_text(encoding='utf-8')))\n"
            "    except Exception:\n"
            "        pass\n"
            "print('\\n'.join(sorted(names)))\n"
        )],
        capture_output=True,
        text=True,
        timeout=60,
    )
    found = set(result.stdout.split()) - {""}
    unlisted = found - set(ENQUEUED_JOB_NAMES)
    assert not unlisted, (
        f"Job names enqueued in src/app but missing from ENQUEUED_JOB_NAMES: {sorted(unlisted)}. "
        "Add them to core/worker/functions.py::ENQUEUED_JOB_NAMES and ensure "
        "WorkerSettings.functions registers them."
    )
