"""Task schemas - job-related data models."""

from pydantic import BaseModel


class Job(BaseModel):
    """Background job/task response schema."""

    id: str


class TaskCreate(BaseModel):
    """Task creation request schema."""

    message: str


class TaskStatus(BaseModel):
    """Task status response schema."""

    id: str
    status: str | None = None
    result: str | None = None
