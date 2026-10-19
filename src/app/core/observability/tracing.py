"""OpenTelemetry tracing configuration.

This module provides utilities for distributed tracing, enabling span
creation and propagation across service boundaries.
"""

import importlib.util
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

# Standard tracer for the application
tracer = trace.get_tracer("fastapi-saas")


def _sdk_is_installed() -> bool:
    """Whether the OpenTelemetry SDK is importable in this interpreter.

    ``opentelemetry-api`` is a declared dependency and is always present; it
    supplies ``ProxyTracer``, which hands back non-recording spans. The SDK is
    what supplies real span implementations, and it is what exports anywhere.

    The ``except`` is load-bearing rather than defensive: ``find_spec`` raises
    ``ValueError`` for a parent package already in ``sys.modules`` with
    ``__spec__ = None``, and raises ``ImportError`` when the parent itself
    cannot be imported. Both mean "not usable", so both mean "no SDK", and a
    half-installed SDK must take the no-op path rather than raise at import
    time and take the whole application down with it.
    """
    try:
        return importlib.util.find_spec("opentelemetry.sdk") is not None
    except (ImportError, ValueError):
        return False


#: Resolved ONCE, at import, deliberately. ``find_spec`` walks import machinery
#: and sys.path, so calling it per span would cost more than the span it skips;
#: the module is imported once per process and installing a package requires a
#: restart anyway, so a per-call re-check could never answer differently.
#: This is the reversible guard the change is built on: install the SDK, restart
#: the process, and every span comes back with no edit here.
_SDK_INSTALLED: bool = _sdk_is_installed()


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Generator[trace.Span]:
    """Create and manage a trace span.

    With no OTel SDK installed this is a genuine no-op rather than a cheap
    stand-in. ``ProxyTracer`` delegates to a ``NoOpTracer`` whose
    ``start_as_current_span`` is a generator that yields ``INVALID_SPAN`` -- a
    module-level singleton built once at import time -- and allocates NOTHING
    per call. What the dead path costs is therefore the context manager
    protocol it enters and throws away, once per database session on every
    request, NOT a span allocation. It exports to nobody either way.

    ``trace.INVALID_SPAN`` is itself a ``NonRecordingSpan``, so yielding it
    directly is the SAME span a caller would have received.

    Both of those are properties of the INSTALLED ``opentelemetry-api``, not of
    this repository. The declared pin is ``>=1.20.0``, a floor rather than an
    exact version, so "what the dead path does" is only ever a statement about
    the release that happens to be resolved. Verified against
    ``opentelemetry-api`` 1.39.1, where ``trace.get_tracer`` returns a
    ``ProxyTracer`` whose inner tracer is a ``NoOpTracer``.
    ``tests/unit/core/observability/test_tracing_noop_allocates_nothing.py``
    pins the behaviour, so a dependency bump that changes it fails loudly
    instead of leaving this paragraph quietly wrong.

    That equivalence is why the error path below is skipped rather than
    reimplemented: ``NonRecordingSpan.set_status`` and ``record_exception`` are
    already no-ops, so dropping the ``try``/``except`` removes work without
    removing behaviour.

    Args:
        name: Name of the span
        attributes: Initial attributes for the span
        kind: Span kind (INTERNAL, SERVER, CLIENT, etc.)

    Yields:
        The active span object
    """
    if not _SDK_INSTALLED:
        yield trace.INVALID_SPAN
        return

    with tracer.start_as_current_span(name, attributes=attributes, kind=kind) as span:
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise


__all__ = ["tracer", "trace_span"]
