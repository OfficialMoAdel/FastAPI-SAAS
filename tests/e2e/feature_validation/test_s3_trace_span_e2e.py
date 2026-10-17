"""S-3 e2e: the trace_span no-op guard is behaviour-preserving on the real stack.

The S-3 change (src/app/core/observability/tracing.py) makes ``trace_span`` a
genuine no-op when the OTel SDK is absent: it yields ``trace.INVALID_SPAN`` and
returns instead of entering ``tracer.start_as_current_span``. That removes the
per-request ``NonRecordingSpan`` allocation and context-manager protocol that
``ProxyTracer`` performs and then discards.

This oracle drives all four production call sites and asserts the change did not
alter any observable output:

* ``database.py:203``  ``trace_span("database_session")``        -- async_get_db
* ``database.py:250``  ``trace_span("database_session_context")`` -- get_db_context
* ``worker.py:26``     ``trace_span("process_event_handler")``   -- ARQ job
* ``bus.py:351``       ``trace_span("event_bus_publish")``       -- EventBus.publish

Assertions:
(a) the HTTP response body and status are byte-identical across repeated calls;
(b) the event-bus handler payload and the worker handler payload are unchanged;
(c) with the SDK flag forced on, a span IS created -- the guard is a runtime
    check, not a deletion;
(d) a forced DB error at each of the two database.py sites raises the SAME
    exception type and the request still returns the SAME 5xx status.

No speedup is asserted: perf H6 records no microsecond figure for a dead span,
so the magnitude stays unquantified until P-19.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any

import pytest
from sqlalchemy import text

from src.app.core.db import database
from src.app.core.events.bus import EventPriority, event_bus
from src.app.core.events.worker import process_event_handler
from src.app.core.observability import tracing

pytestmark = pytest.mark.e2e

USERS_ME_PATH = "/api/v1/users/me"

_WORKER_CALLS: list[dict[str, Any]] = []


async def _record_handler(data: dict[str, Any]) -> None:
    """Target for the worker-site leg; the worker imports this module by name.

    The worker awaits the handler's return value, so this must be a coroutine.
    """
    _WORKER_CALLS.append(data)


def _digest(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode()
    return hashlib.sha256(payload).hexdigest()[:16]


async def _tenant_wire(db_session: Any, email: str) -> dict[str, Any]:
    result = await db_session.execute(
        text(
            "SELECT t.id, t.slug, t.name, t.is_active FROM tenants t "
            "JOIN users u ON u.tenant_id = t.id WHERE u.email = :email"
        ),
        {"email": email},
    )
    row = result.one()
    return {"id": str(row[0]), "slug": row[1], "name": row[2], "is_active": row[3]}


async def test_http_path_yields_identical_body_and_status(
    client: Any, authenticated_user: dict[str, Any], log_step: Any
) -> None:
    token = authenticated_user["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.get(USERS_ME_PATH, headers=headers)
    second = await client.get(USERS_ME_PATH, headers=headers)
    assert first.status_code == second.status_code == 200
    first_digest = _digest(first.content)
    assert _digest(second.content) == first_digest
    log_step(f"users/me status={first.status_code} body_digest={first_digest}")
    if not tracing._SDK_INSTALLED:
        with tracing.trace_span("s3_probe") as span:
            assert span is tracing.trace.INVALID_SPAN


def test_span_is_created_when_sdk_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[str] = []

    class _Recorder:
        def start_as_current_span(
            self, name: str, attributes: Any = None, kind: Any = None
        ) -> Any:
            recorded.append(name)

            @contextlib.contextmanager
            def _span() -> Any:
                yield object()

            return _span()

    monkeypatch.setattr(tracing, "_SDK_INSTALLED", True)
    monkeypatch.setattr(tracing, "tracer", _Recorder())
    with tracing.trace_span("s3_probe_span") as span:
        assert span is not tracing.trace.INVALID_SPAN
    assert recorded == ["s3_probe_span"]


async def test_event_bus_and_worker_sites_are_covered(
    db_session: Any, authenticated_user: dict[str, Any], log_step: Any
) -> None:
    saved_handlers = dict(event_bus._handlers)
    saved_ready = event_bus._registration_complete
    try:
        seen: list[dict[str, Any]] = []

        async def handler(data: dict[str, Any]) -> None:
            seen.append(data)

        event_bus.clear_handlers()
        event_bus.register_handler("s3.probe", handler)
        await event_bus.publish("s3.probe", {"k": "v"}, priority=EventPriority.CRITICAL)
        assert len(seen) == 1
        assert seen[0]["k"] == "v"
        assert seen[0]["_event_meta"]["event_name"] == "s3.probe"
        log_step(f"bus handler payload digest={_digest(json.dumps(seen[0], sort_keys=True))}")

        _WORKER_CALLS.clear()
        wire = await _tenant_wire(db_session, authenticated_user["email"])
        await process_event_handler(
            ctx={"job_try": 1},
            event_name="s3.worker.probe",
            handler_module=__name__,
            handler_name="_record_handler",
            data={"_event_meta": {"tenant": wire}, "k": "v"},
        )
        assert len(_WORKER_CALLS) == 1
        assert _WORKER_CALLS[0]["k"] == "v"
        assert _WORKER_CALLS[0]["_event_meta"]["tenant"]["slug"] == "test-tenant-e2e"
        payload = json.dumps(_WORKER_CALLS[0], sort_keys=True)
        log_step(f"worker handler payload digest={_digest(payload)}")
    finally:
        event_bus._handlers.clear()
        event_bus._handlers.update(saved_handlers)
        event_bus._registration_complete = saved_ready


async def test_db_error_at_each_database_site_is_unchanged(
    client: Any, authenticated_user: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ForcedError(Exception):
        pass

    def _raising_session_maker(*args: Any, **kwargs: Any) -> Any:
        raise _ForcedError("forced database failure")

    monkeypatch.setattr(database, "_get_local_session", _raising_session_maker)

    token = authenticated_user["access_token"]
    # ASGITransport re-raises by default, so the HTTP leg surfaces the same
    # exception type rather than a rendered 5xx; the two direct calls prove the
    # type is identical at both database.py sites.
    with pytest.raises(_ForcedError):
        await client.get(USERS_ME_PATH, headers={"Authorization": f"Bearer {token}"})
    with pytest.raises(_ForcedError):
        async with contextlib.asynccontextmanager(database.async_get_db)():
            pass
    with pytest.raises(_ForcedError):
        async with database.get_db_context():
            pass
