"""Tasks module - background job management.

Direct imports:
    from src.app.modules.tasks.service import task_service
    from src.app.modules.tasks.router import router as tasks_router
    from src.app.modules.tasks.schemas import Job
"""

__all__ = [
    "Job",
    "task_service",
    "tasks_router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "tasks_router":
        from src.app.modules.tasks.router import router as tasks_router

        return tasks_router
    if name == "task_service":
        from src.app.modules.tasks.service import task_service

        return task_service
    if name == "Job":
        from src.app.modules.tasks.schemas import Job

        return Job
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
