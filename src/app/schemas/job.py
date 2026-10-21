from datetime import datetime

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    status: str
    result: str | None = None
    duration_seconds: float | None = None
    job_id: str | None = None
    function: str | None = None
    enqueue_time: datetime | None = None
    start_time: datetime | None = None
    finish_time: datetime | None = None


class JobMetrics(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0
    total_duration: float = 0.0
    avg_duration: float = 0.0


class JobMetricsResponse(BaseModel):
    metrics: dict[str, JobMetrics]


class DLQItemResponse(BaseModel):
    job_id: str | None = None
    function: str | None = None
    error: str | None = None
    # F3: the DLQ keeps rows it could not parse — `{"raw": …, "error":
    # "parse_error"}` — and those carry NEITHER timestamp. The fields were
    # required, so ONE malformed row raised a pydantic ValidationError and
    # took the WHOLE admin page down, at exactly the moment the operator
    # needed to see what was in the queue.
    timestamp: float | None = None
    timestamp_iso: str | None = None
    raw: str | None = None  # For malformed items


class JobSummaryResponse(BaseModel):
    total_jobs_processed: int
    total_failures: int
    active_functions: list[str]
    dlq_size: int
    status: str
