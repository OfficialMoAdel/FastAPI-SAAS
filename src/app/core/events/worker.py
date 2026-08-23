"""ARQ worker entry point for queued event handlers."""

import asyncio
from typing import Any

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
        meta = data.get("_event_meta", {})
        tenant_info = meta.get("tenant")
        if tenant_info:
            try:
                from uuid import UUID

                from src.app.core.tenancy.context import TenantContext, set_current_tenant

                if not all(tenant_info.get(k) is not None for k in ("id", "slug", "name", "is_active")):
                    raise KeyError(f"Missing or null required keys in tenant_info: {tenant_info}")

                tenant_context = TenantContext(
                    tenant_id=UUID(tenant_info["id"]),
                    tenant_slug=tenant_info["slug"],
                    tenant_name=tenant_info["name"],
                    is_active=tenant_info["is_active"],
                )
                set_current_tenant(tenant_context)
                _pkg.logger.debug("Tenant context restored for background task", tenant_id=tenant_info["id"])
            except Exception as e:
                _pkg.logger.error("Failed to restore tenant context in worker - moving to DLQ", error=str(e))
                await _pkg.event_bus.add_to_dlq(
                    event_name=event_name,
                    handler_name=f"{handler_module}.{handler_name}",
                    data=data,
                    error=f"Malformed tenant context: {e}",
                    attempt_count=job_try,
                )
                return

        try:
            # Dynamically import and execute handler
            import importlib

            module = importlib.import_module(handler_module)
            handler = getattr(module, handler_name)

            # Apply timeout protection for async worker execution
            try:
                timeout_val = _pkg.settings.EVENT_HANDLER_TIMEOUT_SECONDS
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
            raise  # Re-raise for ARQ retry logic
        finally:
            # SECURITY FIX: Always clear context to prevent leakage between jobs in the same worker
            from src.app.core.tenancy.context import clear_tenant_context

            clear_tenant_context()
            _pkg.logger.debug("Tenant context cleared after background task")
