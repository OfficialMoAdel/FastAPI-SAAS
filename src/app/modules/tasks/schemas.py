"""Task schemas - job-related data models."""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

#: A background task's message is hashed into the deterministic job id AND
#: stored verbatim as an ARQ job argument in Redis, so it needs an upper bound:
#: without one the only ceiling was the 10 MiB request-body limit, and every
#: queued job carried its own copy of it. 4 KiB matches the module's intended
#: "small unit of work" shape and keeps the same discipline every other
#: free-text schema in the repo declares (posts, ai, notifications, tenants).
MAX_TASK_MESSAGE_LENGTH = 4096

TaskMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TASK_MESSAGE_LENGTH),
]


class Job(BaseModel):
    """Background job/task response schema."""

    id: str


class TaskCreate(BaseModel):
    """Task creation request schema."""

    message: TaskMessage


class TaskStatus(BaseModel):
    """Task status response schema."""

    id: str
    status: str | None = None
    result: str | None = None
