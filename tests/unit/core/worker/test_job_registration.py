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


def test_module_registration_lists_aggregate_into_worker_settings():
    """A-M3: per-module ARQ_FUNCTIONS/ARQ_CRONJOBS declarations reach the registry.

    The discovery walks ``src.app.modules.*`` package roots and merges any
    ``ARQ_FUNCTIONS``/``ARQ_CRONJOBS`` lists into WorkerSettings. Today no
    module declares them (the manual core list is the whole registry), so
    this asserts the merge contract directly: injecting a declaration into
    a module's namespace must surface the function and cron job in the
    aggregated result, and a duplicate core name must NOT be re-registered.
    """
    from unittest.mock import patch

    import src.app.core.worker.settings as worker_settings_module
    from src.app.modules import health  # a module package root

    async def _fake_task(ctx: dict) -> str:  # pragma: no cover - never invoked
        return "ok"

    fake_cron = object()
    with (
        patch.object(health, "ARQ_FUNCTIONS", [_fake_task], create=True),
        patch.object(health, "ARQ_CRONJOBS", [fake_cron], create=True),
        patch.object(
            worker_settings_module,
            "_MODULE_FUNCTIONS",
            worker_settings_module._discover_module_registrations(),
        ),
    ):
        # Re-discover with the injected declarations in place.
        functions, cron_jobs = worker_settings_module._discover_module_registrations()
        assert any(getattr(f, "__name__", "") == "_fake_task" for f in functions)
        assert fake_cron in cron_jobs

    # Duplicate suppression: a module re-declaring a core function is skipped.
    from src.app.modules.notifications.service import send_email_task

    merged = worker_settings_module._merge_module_functions([send_email_task, _fake_task])
    merged_again = worker_settings_module._merge_module_functions(merged)
    assert merged == merged_again, "re-merging an already-merged list must be a no-op"
    names = [getattr(f, "__name__", str(f)) for f in merged]
    assert names.count("send_email_task") == 1


def test_worker_settings_registry_is_complete_after_merge():
    """The composed registry still covers every runtime-enqueued job name."""
    from src.app.core.worker.settings import WorkerSettings

    names = {getattr(f, "__name__", str(f)) for f in WorkerSettings.functions}
    assert names >= set(ENQUEUED_JOB_NAMES)


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
        [
            sys.executable,
            "-c",
            (
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
            ),
        ],
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
