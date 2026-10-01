"""Unit-level pins for the monitoring redaction contract.

The e2e suite proves the wire behaviour. These tests pin the two things
that make it structural rather than incidental:

1. No monitoring response schema can carry a raw exception field on the
   probe routes (a model-level constraint, not a per-value assertion).
2. The probe helpers themselves — the functions that build the ``error``
   value — reduce exception text to a class name, whatever they are handed.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.app.api.monitoring import _class_name_of, health_check
from src.app.api.monitoring_schemas import (
    HealthCheckResponse,
    MetricsErrorResponse,
    ProbeCheck,
)

SECRETS = ("hunter2", "postgresql://", "SELECT 1", "db-host.internal", "password=")


def test_probe_schema_has_no_field_for_raw_exception_text() -> None:
    """ProbeCheck models class names only; there is no raw-error field.

    If a future field could hold exception text, it would be a declared,
    typed, reviewable addition here — which is the point. The
    operator-only ``error_detail`` name is asserted absent so the two
    shapes cannot be confused at a call site.
    """
    fields = set(ProbeCheck.model_fields)
    assert "error_detail" not in fields
    assert "exception" not in fields
    assert "traceback" not in fields
    assert "message" in fields, "static operator copy (e.g. 'not configured') is still allowed"

    probe = ProbeCheck(status="unhealthy", error="OperationalError")
    dumped = probe.model_dump(exclude_none=True)
    assert dumped == {"status": "unhealthy", "error": "OperationalError"}


def test_health_response_checks_use_the_probe_schema() -> None:
    """The container is typed with ProbeCheck, so a raw field cannot sneak in."""
    field = HealthCheckResponse.model_fields["checks"]
    assert field.annotation == dict[str, ProbeCheck]


def test_metrics_error_response_is_the_only_shape_carrying_full_text() -> None:
    """``error_detail`` lives on the operator-only envelope and nowhere else."""
    assert "error_detail" in MetricsErrorResponse.model_fields
    envelope = MetricsErrorResponse(error_type="RuntimeError", error_detail="dsn://u:p@h/db")
    assert envelope.error_detail == "dsn://u:p@h/db"
    # A ProbeCheck cannot be built from a MetricsErrorResponse, so the
    # full-text field can never be smuggled onto a probe response.
    assert not set(MetricsErrorResponse.model_fields) <= set(ProbeCheck.model_fields)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("OperationalError: (psycopg) failed\n[SQL: SELECT 1]\ndsn://u:p@h", "OperationalError"),
        ("Unexpected: ValueError: bad", "ValueError"),
        ("ConnectionRefusedError: refused", "ConnectionRefusedError"),
        ("Database check timed out. Timeout after 5.0s", "ProbeError"),
        ("password=hunter2", "ProbeError"),
        ("", "ProbeError"),
    ],
)
def test_class_name_of_reduces_text_to_a_class_name(raw: str, expected: str) -> None:
    """Only a capitalised identifier survives; everything else is dropped."""
    assert _class_name_of(raw) == expected
    for secret in SECRETS:
        assert secret not in _class_name_of(raw)


@pytest.mark.asyncio
async def test_health_check_probe_failures_carry_class_names_only() -> None:
    """Each probe's own error value is a class name, not a message."""
    from sqlalchemy.exc import OperationalError

    db_error = OperationalError("SELECT 1", {}, Exception("dsn://svc_app:hunter2@db-host.internal/appdb"))

    with (
        patch("src.app.api.monitoring.check_database", new_callable=AsyncMock) as db,
        patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock) as redis_probe,
        patch("src.app.api.monitoring.check_worker_queue", new_callable=AsyncMock) as queue,
        patch("src.app.api.monitoring.check_cache_metrics", new_callable=AsyncMock) as cache,
        patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock) as dlq,
    ):
        db.return_value = {"status": "unhealthy", "error": _class_name_of(str(db_error))}
        redis_probe.return_value = {"status": "unhealthy", "error": "ConnectionRefusedError"}
        queue.return_value = {"status": "unhealthy", "error": "TimeoutError", "queue_size": 0}
        cache.return_value = {"status": "healthy", "xfetch_hit_rate": 0.0, "rbac_hit_rate": 0.0}
        dlq.side_effect = ValueError("dsn://svc_app:hunter2@db-host.internal/appdb")

        result = await health_check()

    assert result.status == "error", "an unhealthy component must still fail the probe"
    body = result.model_dump_json()
    for secret in SECRETS:
        assert secret not in body
    assert "error_detail" not in body


@pytest.mark.asyncio
async def test_probe_helper_never_echoes_exception_text() -> None:
    """check_redis / check_worker_queue catch-and-report paths stay redacted."""
    from src.app.api.monitoring import check_dlq_size, check_redis, check_worker_queue

    redis_client = AsyncMock()
    redis_client.ping.side_effect = ConnectionRefusedError("redis connect user=svc-cache password=hunter2")
    with patch("src.app.core.redis_provider.get_redis", return_value=redis_client):
        redis_result = await check_redis()

    assert redis_result["error"] == "ConnectionRefusedError"

    with patch(
        "src.app.core.utils.queue.get_queue_size",
        new=AsyncMock(side_effect=TimeoutError("queue password=hunter2")),
    ):
        queue_result = await check_worker_queue()

    assert queue_result["error"] == "TimeoutError"

    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError("dsn://u:p@h/db")):
        dlq_result = await check_dlq_size()

    assert dlq_result["error"] == "RuntimeError"
    for result in (redis_result, queue_result, dlq_result):
        assert "hunter2" not in str(result)
        assert "dsn://" not in str(result)


@pytest.mark.asyncio
async def test_health_check_with_no_failures_reports_ok() -> None:
    """A clean probe set still reports ok — the model_validate reshape is lossless."""
    healthy = {"status": "healthy"}
    with (
        patch("src.app.api.monitoring.check_database", new_callable=AsyncMock, return_value=healthy),
        patch("src.app.api.monitoring.check_redis", new_callable=AsyncMock, return_value=healthy),
        patch("src.app.api.monitoring.check_worker_queue", new_callable=AsyncMock, return_value=healthy),
        patch("src.app.api.monitoring.check_cache_metrics", new_callable=AsyncMock, return_value=healthy),
        patch("src.app.api.monitoring.check_dlq_size", new_callable=AsyncMock, return_value=healthy),
    ):
        result = await health_check()

    assert result.status == "ok"
    assert set(result.checks) == {"database", "redis", "worker_queue", "cache_metrics", "dlq_size"}


@pytest.mark.asyncio
async def test_metrics_failure_returns_operator_envelope() -> None:
    """The superuser path keeps the full text, under a distinct field name."""
    from src.app.api.monitoring import metrics_export, metrics_json

    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError("dsn://u:p@h/db")):
        responses = [await metrics_export(), await metrics_json()]

    for response in responses:
        assert response.status_code == 500
        assert json.loads(bytes(response.body)) == {
            "status": "error",
            "error_type": "RuntimeError",
            "error_detail": "dsn://u:p@h/db",
            "request_id": None,
        }
