"""The LOW-priority ARQ worker.

arq polls exactly ONE queue per worker process, so ``EventPriority.LOW``
events — enqueued to ``arq:queue_low_priority`` — need a second worker. These
tests pin the two properties that make running it safe:

- the option mirror stays COMPLETE. arq's ``get_kwargs`` reads
  ``settings_cls.__dict__``, which holds only the attributes defined on the
  class itself, so an inherited attribute would be dropped and the worker
  would start with no functions at all;
- the cron schedule stays with the primary worker — two workers running the
  cron schedules would execute them twice.
"""

import inspect

from arq.constants import default_queue_name
from arq.worker import Worker

from src.app.core.worker.settings import LowPriorityWorkerSettings, WorkerSettings


def test_mirror_covers_every_worker_option() -> None:
    """Every option the primary worker sets must be set here too."""
    worker_options = set(inspect.signature(Worker).parameters)
    missing = {
        name
        for name in vars(WorkerSettings)
        if name in worker_options and name not in vars(LowPriorityWorkerSettings)
    }
    assert not missing, f"LowPriorityWorkerSettings is missing worker options: {sorted(missing)}"


def test_low_priority_queue_name() -> None:
    """The worker polls the queue the LOW-priority events are enqueued to."""
    assert LowPriorityWorkerSettings.queue_name == f"{default_queue_name}_low_priority"
    assert LowPriorityWorkerSettings.queue_name != default_queue_name


def test_cron_jobs_belong_to_the_primary_worker() -> None:
    """Two workers running the cron schedules would execute them twice."""
    assert LowPriorityWorkerSettings.cron_jobs == []
    assert WorkerSettings.cron_jobs, "the primary worker must still own the cron schedule"


def test_low_priority_worker_can_run_event_handlers() -> None:
    """The LOW queue carries `process_event_handler` jobs."""
    names = {getattr(fn, "__name__", str(fn)) for fn in LowPriorityWorkerSettings.functions}
    assert "process_event_handler" in names
    assert names == {getattr(fn, "__name__", str(fn)) for fn in WorkerSettings.functions}
