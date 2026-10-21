"""ARQ worker entry point for queued event handlers."""

import asyncio
from typing import Any

from arq.worker import Retry

from src.app.core import events as _pkg


async def process_event_handler(
    ctx: dict[str, Any],
    event_name: str,
    handler_module: str,
    handler_name: str,
    data: dict[str, Any],
) -> None:
    """ARQ worker function to execute event handlers asynchronously.

    Args:
        ctx: ARQ context (includes job_try for retry count)
        event_name: Name of event
        handler_module: Module containing handler
        handler_name: Name of handler function
        data: Event payload
    """
    job_try = ctx.get("job_try", 1)
    # A4: the backoff schedule is indexed by `job_try - 1`. A malformed ctx
    # (job_try 0 or negative) would index -1 and select the LONGEST delay,
    # and a non-int would break the comparison outright — so the counter is
    # clamped to the first attempt.
    if not isinstance(job_try, int) or job_try < 1:
        job_try = 1
    with _pkg.trace_span(
        "process_event_handler",
        attributes={
            "event_name": event_name,
            "handler": f"{handler_module}.{handler_name}",
            "job_try": job_try,
        },
    ):
        # SECURITY FIX: Restore tenant context from event metadata
        # This ensures RLS-protected operations in background tasks work correctly.
        # Packing/parsing both live behind the tenancy lifecycle seam, so the
        # wire format cannot drift from what the event bus publishes.
        from src.app.core.tenancy.lifecycle import TenantWireError, bind_tenant, tenant_from_wire

        meta = data.get("_event_meta", {})
        try:
            bind_tenant(tenant_from_wire(meta.get("tenant")))
        except TenantWireError as e:
            _pkg.logger.error("Failed to restore tenant context in worker - moving to DLQ", error=str(e))
            await _pkg.event_bus.add_to_dlq(
                event_name=event_name,
                handler_name=f"{handler_module}.{handler_name}",
                data=data,
                error=f"Malformed tenant context: {e}",
                attempt_count=job_try,
            )
            # F32: this early return skipped the finally below, leaking the
            # previous job's tenant into the next one on this worker. bind
            # failed so nothing new was installed, but unbind anyway — it is
            # idempotent and closes the leak even if a prior job misbehaved.
            from src.app.core.tenancy.lifecycle import unbind_tenant as _unbind

            _unbind()
            return
        _pkg.logger.debug("Tenant context restored for background task")

        try:
            # Dynamically import and execute handler
            import importlib

            module = importlib.import_module(handler_module)
            handler = getattr(module, handler_name)

            # A1: read the timeout BEFORE the inner try, so the TimeoutError
            # handler below can never reference an unbound name. A settings
            # read that raised TimeoutError used to surface as
            # UnboundLocalError and DLQ the event as an opaque generic
            # failure, losing the timeout diagnosis.
            timeout_val = _pkg.settings.events.EVENT_HANDLER_TIMEOUT_SECONDS

            # Apply timeout protection for async worker execution
            try:
                await asyncio.wait_for(
                    handler(data),
                    timeout=timeout_val,
                )
            except TimeoutError:
                _pkg.logger.error(
                    "ARQ event handler timed out",
                    event_name=event_name,
                    handler=f"{handler_module}.{handler_name}",
                    timeout=timeout_val,
                )
                raise TimeoutError(f"Handler timed out after {timeout_val}s")

            _pkg.logger.info(
                "ARQ event handler success",
                event_name=event_name,
                handler=f"{handler_module}.{handler_name}",
            )
        except (ImportError, AttributeError) as e:
            # ISSUE 10 FIX: Do not retry on permanent configuration/import errors
            handler_full_name = f"{handler_module}.{handler_name}"
            _pkg.logger.error(
                "Critical configuration error in ARQ handler - will not retry",
                event_name=event_name,
                handler=handler_full_name,
                error=str(e),
            )
            # Move to DLQ immediately
            await _pkg.event_bus.add_to_dlq(
                event_name=event_name,
                handler_name=handler_full_name,
                data=data,
                error=f"Permanent Error: {e}",
                attempt_count=job_try,
            )
            return

        except Exception as e:
            handler_full_name = f"{handler_module}.{handler_name}"

            # Check if max retries exceeded
            if job_try >= _pkg.EventBus.MAX_RETRIES:
                _pkg.logger.error(
                    "ARQ event handler max retries exceeded - moving to DLQ",
                    event_name=event_name,
                    handler=handler_full_name,
                    attempt_count=job_try,
                    error=str(e),
                )
                # Add to dead-letter queue
                await _pkg.event_bus.add_to_dlq(
                    event_name=event_name,
                    handler_name=handler_full_name,
                    data=data,
                    error=str(e),
                    attempt_count=job_try,
                )
                # Don't re-raise - event is now in DLQ
                return

            _pkg.logger.warning(
                "ARQ event handler failed - will retry",
                event_name=event_name,
                handler=handler_full_name,
                attempt=job_try,
                max_attempts=_pkg.EventBus.MAX_RETRIES,
                error=str(e),
            )
            # K3 FIX: arq only re-runs jobs that raise arq.worker.Retry (or
            # CancelledError/RetryJob). A bare raise of a plain exception sets
            # result=e, finish=True and DELETES the job -> zero retries. Raise
            # Retry with the configured backoff so the job re-runs.
            # F10: resolve the backoff live (same fallback the bus uses) — the
            # old class-attr read ignored EVENT_BUS_RETRY_DELAYS overrides, so
            # web and worker disagreed on retry cadence. The knob lives on the
            # `events` component, so it is read through that namespace (H-7);
            # an empty schedule falls back to the class default because the
            # schedule is indexed below.
            delays = list(_pkg.settings.events.EVENT_BUS_RETRY_DELAYS) or list(_pkg.EventBus.RETRY_DELAYS)
            raise Retry(defer=delays[min(job_try - 1, len(delays) - 1)]) from e
        finally:
            # SECURITY FIX: Always clear context to prevent leakage between jobs in the same worker
            from src.app.core.tenancy.lifecycle import unbind_tenant

            unbind_tenant()
            _pkg.logger.debug("Tenant context cleared after background task")
