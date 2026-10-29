"""Task schemas - job-related data models."""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

TaskMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
