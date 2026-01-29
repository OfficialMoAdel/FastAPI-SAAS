from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from src.app.core.job_monitoring import get_job_monitor
from src.app.schemas.job import (
    DLQItemResponse,
    JobMetrics,
    JobMetricsResponse,
    JobStatusResponse,
    JobSummaryResponse,
)

router = APIRouter()


@router.get("/summary", response_model=JobSummaryResponse)
async def get_jobs_summary():
    """
    Get a high-level summary of job system health.
    """
    monitor = get_job_monitor()
    return await monitor.get_summary()


@router.get("/metrics", response_model=JobMetricsResponse)
async def get_jobs_metrics(function_name: Optional[str] = None):
    """
    Get aggregated metrics for job functions.
    If function_name is provided, returns metrics only for that function.
    """
    monitor = get_job_monitor()
    metrics_data = await monitor.get_job_metrics()

    # Convert dicts to Pydantic models
    metrics_models = {k: JobMetrics(**v) for k, v in metrics_data.items()}

    if function_name:
        if function_name in metrics_models:
            return JobMetricsResponse(metrics={function_name: metrics_models[function_name]})
        return JobMetricsResponse(metrics={})

    return JobMetricsResponse(metrics=metrics_models)


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """
    Get the status and details of a specific job.
    """
    monitor = get_job_monitor()
    job_data = await monitor.get_job_status(job_id)

    if job_data.get("status") == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

    if "error" in job_data and job_data["status"] == "unknown":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=job_data["error"])

    return job_data


@router.get("/dlq", response_model=list[DLQItemResponse])
async def get_dlq_items(
    limit: int = Query(100, ge=1, le=1000, description="Number of items to retrieve"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Get items from the Dead Letter Queue.
    """
    monitor = get_job_monitor()
    return await monitor.get_dead_letter_queue(limit=limit, offset=offset)


@router.delete("/dlq", status_code=status.HTTP_204_NO_CONTENT)
async def clear_dlq():
    """
    Clear the Dead Letter Queue.
    """
    monitor = get_job_monitor()
    success = await monitor.clear_dlq()
    if not success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to clear DLQ (Redis unavailable)"
        )
