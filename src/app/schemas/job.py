from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    status: str
    result: Optional[str] = None
    duration_seconds: Optional[float] = None
    job_id: Optional[str] = None
    function: Optional[str] = None
    enqueue_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    finish_time: Optional[datetime] = None


class JobMetrics(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0
    total_duration: float = 0.0
    avg_duration: float = 0.0


class JobMetricsResponse(BaseModel):
    metrics: dict[str, JobMetrics]


class DLQItemResponse(BaseModel):
    job_id: Optional[str] = None
    function: Optional[str] = None
    error: Optional[str] = None
    timestamp: float
    timestamp_iso: str
    raw: Optional[str] = None  # For malformed items


class JobSummaryResponse(BaseModel):
    total_jobs_processed: int
    total_failures: int
    active_functions: list[str]
    dlq_size: int
    status: str
