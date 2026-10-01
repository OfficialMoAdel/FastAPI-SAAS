# Tasks Module AGENTS.md

## Package Identity
Background job processing via ARQ. Manages job creation, status tracking, and retry logic for async tasks.

## Setup & Run
```bash
# Start ARQ worker
uv run arq src.app.modules.tasks.worker

# Run tests
uv run pytest tests/unit/modules/tasks/test_service.py -v

# Type check
uv run mypy src/app/modules/tasks/

# Lint
uv run ruff check src/app/modules/tasks/
```

## Patterns & Conventions

### Code Organization
- TaskService for job creation (enqueue) - simple wrapper around ARQ
- Worker execution handled by ARQ directly
- Job status tracking via ARQ/Redis (stateless service)
- Retry logic handled by ARQ configuration

### Job Queueing Pattern
```python
# Pattern from src/app/modules/tasks/service.py:

async def create_task(self, message: str) -> dict[str, str]:
    """Create a new background task."""
    if queue.pool is None:
        # QueueUnavailableError derives from ServiceUnavailableError —
        # the central EXCEPTION_HANDLERS table maps it to 503.
        raise QueueUnavailableError("Queue is not available")

    job = await queue.pool.enqueue_job("sample_background_task", message)
    if job is None:
        raise ValueError("Failed to create task")

    return {"id": job.job_id}
```

### Worker Pattern
```python
# Worker tasks are defined in src/app/worker.py or similar entry point
# Tasks are simple async functions decorated with @worker_task
async def sample_background_task(ctx, message: str):
    # Process message
    pass
```

### Retry Logic
- Transient errors: Retry with exponential backoff
- Max retries: 3 attempts (configurable)
- Retry delay: 2^n seconds (2s, 4s, 8s)
- Permanent errors: Mark as failed immediately

## Touch Points / Key Files

- Service: `src/app/modules/tasks/service.py` - Job creation
- Router: `src/app/modules/tasks/router.py` - API endpoints

## JIT Index Hints

- Find enqueue operations: `rg -n "enqueue" src/app/modules/tasks/`
- Find job execution: `rg -n "async def" src/app/modules/tasks/`
- Find tests: `find tests -name "*tasks*"`

## Common Gotchas

- TaskService is a simple wrapper, not a full CRUD service
- Jobs are ephemeral (Redis-backed)
- No persistent history in database by default

## Pre-PR Checks

```bash
uv run mypy src/app/modules/tasks/ && uv run ruff check src/app/modules/tasks/ && uv run pytest tests/unit/modules/tasks/test_service.py -v
```
