"""Worker job-registration contract tests (candidate 10, B8 bug class).

The B8 bug: ``cleanup_leaked_webhook_lock`` was ENQUEUED by the web process
but not DEFINED/registered on the ARQ worker — a job name missing in one of
the two places. ``assert_all_jobs_registered`` compares the static set of
runtime-enqueued job names against WorkerSettings.functions; wired into
worker startup as a hard failure so the miss can never ship silently again.

The registry invariant has two directions and they are enforced in two places.
Enqueued -> registered is the B8 guard above, and it runs at worker startup
because a miss there is an outage. Registered -> enqueued|cron|manual is
enforced here instead, in-process, because a miss there is drift rather than
an outage: a job that is registered but that nothing schedules has not broken
anything yet, and failing worker startup over it would turn every future
"registered but not wired yet" into a production incident.
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
    """worker.startup must CALL the guard — the B8 class must fail startup.

    Was a source grep ("<name>" in inspect.getsource(startup)), which still
    passes when the call is deleted and only a comment keeps the name. The
    falsifiability proof lives in test_startup_wiring_ast.py.
    """
    import ast
    import textwrap

    from src.app.core.worker import functions as worker_functions

    tree = ast.parse(textwrap.dedent(inspect.getsource(worker_functions.startup)))
    called = {
        n.func.id if isinstance(n.func, ast.Name) else n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name | ast.Attribute)
    }
    assert "assert_all_jobs_registered" in called, (
        "worker.startup does not CALL assert_all_jobs_registered()"
    )


def test_worker_settings_registry_is_the_only_registry():
    """The explicit registry still covers every runtime-enqueued job name."""
    from src.app.core.worker.settings import WorkerSettings

    names = {getattr(f, "__name__", str(f)) for f in WorkerSettings.functions}
    assert names >= set(ENQUEUED_JOB_NAMES)


# Registered on the worker but triggered by an operator, not enqueued by the
# web process and not on a schedule. migrate_sessions_task runs the one-shot
# session_manager.migrate_to_hash() data migration and is reachable only by
# invoking the registered job directly. Declared rather than deleted: it is the
# only production path to that migration, and the invariant below cannot tell
# "intentionally manual" from "forgotten to wire" without someone saying so.
MANUAL_INVOCATION_JOBS = {"migrate_sessions_task"}


def _registered_names() -> set[str]:
    from src.app.core.worker.settings import WorkerSettings

    return {getattr(f, "__name__", str(f)) for f in WorkerSettings.functions}


def _cron_names() -> set[str]:
    from src.app.core.worker.settings import WorkerSettings

    return {getattr(c.coroutine, "__name__", str(c.coroutine)) for c in WorkerSettings.cron_jobs}


def test_every_registered_job_is_enqueued_croned_or_manual():
    """The other half of the registry invariant, checked in-process.

    assert_all_jobs_registered covers enqueued -> registered (the B8 class) and
    runs at worker startup. This covers registered -> enqueued|cron|manual, so
    a function that is registered but that nothing ever schedules cannot hide.

    Replaces a test that shelled out to a subprocess and regex-scanned the
    source tree for literal ``enqueue_job("name"`` calls. That scan only saw
    *literal* call sites, and its only unique case — registered but missing
    from ENQUEUED_JOB_NAMES — is exactly what this asserts, without the
    60-second subprocess.
    """
    unscheduled = _registered_names() - set(ENQUEUED_JOB_NAMES) - _cron_names() - MANUAL_INVOCATION_JOBS
    assert not unscheduled, (
        f"Registered worker functions that nothing enqueues, schedules, or "
        f"declares manual: {sorted(unscheduled)}. Either enqueue the name (and "
        "add it to core/worker/functions.py::ENQUEUED_JOB_NAMES), add it to "
        "WorkerSettings.cron_jobs, or list it in MANUAL_INVOCATION_JOBS if an "
        "operator triggers it deliberately."
    )


def test_every_cron_job_is_registered():
    """A cron entry whose coroutine is not registered can never run."""
    unregistered = _cron_names() - _registered_names()
    assert not unregistered, f"Cron jobs missing from WorkerSettings.functions: {sorted(unregistered)}"


def test_manual_invocation_jobs_are_still_registered():
    """The exemption set cannot rot into naming a function that no longer exists."""
    unknown = MANUAL_INVOCATION_JOBS - _registered_names()
    assert not unknown, (
        f"MANUAL_INVOCATION_JOBS names unregistered functions: {sorted(unknown)}. "
        "Remove them from the set — the job was deleted or renamed."
    )
