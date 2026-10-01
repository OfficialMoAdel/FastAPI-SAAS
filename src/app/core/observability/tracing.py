"""OpenTelemetry tracing configuration.

This module provides utilities for distributed tracing, enabling span
creation and propagation across service boundaries.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Standard tracer for the application
tracer = trace.get_tracer("fastapi-saas")


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Generator[trace.Span]:
    """Create and manage a trace span.

    Args:
        name: Name of the span
        attributes: Initial attributes for the span
        kind: Span kind (INTERNAL, SERVER, CLIENT, etc.)

    Yields:
        The active span object
    """
    with tracer.start_as_current_span(name, attributes=attributes, kind=kind) as span:
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


__all__ = ["tracer", "trace_span"]
