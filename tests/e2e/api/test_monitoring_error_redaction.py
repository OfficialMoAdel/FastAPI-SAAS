"""Probe responses must not publish raw exception text; /metrics still must.

The leak this pins: ``/api/health`` returned ``"error": str(e)`` inside a
200 body, so a caller who supplied a tenant slug could read the failed
statement, the DSN, and the connection password out of a health check. The
tenant middleware 400s a request with no ``X-Tenant-ID`` (the exempt set
lists ``/health``, not ``/api/health``), so reaching the probe requires
supplying a guessable tenant slug. That makes this a defence-in-depth
issue, MEDIUM: reachable by any caller who can name a tenant, but not
reached by a caller presenting no tenant header at all.

Every secret assertion here is an ABSENCE assertion. A test that only checked
"the class name is present" would pass against the leaky code, because the
leaky code returns the class name AND the text; only "the DSN is absent"
catches the regression.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from tests.conftest import MockUser
from tests.helpers.auth import override_test_principal

TENANT_HEADERS = {"X-Tenant-ID": "test-tenant"}

# The two real-world shapes that must never be echoed back to a caller.
REDIS_LEAK = (
    "redis connect host=cache-host.internal port=6379 user=svc-cache password=hunter2 refused"
)
DB_LEAK = (
    "OperationalError: (psycopg.OperationalError) connection failed: "
    'could not translate host name "db-host.internal" to address: lookup failed\n'
    "[SQL: SELECT 1]\n"
    "postgresql://svc_app:hunter2@db-host.internal:5432/appdb"
)

# Substrings that must NOT appear in any non-superuser body. One entry per
# secret class, so scrubbing one cannot mask a leak of another.
FORBIDDEN_SUBSTRINGS = (
    "hunter2",
    "db-host.internal",
    "cache-host.internal",
    "postgresql://",
    "SELECT 1",
    "svc_app",
    "svc-cache",
    "could not translate",
    "psycopg.OperationalError",
    "password=",
)


def _assert_no_internals(body: str) -> None:
    for needle in FORBIDDEN_SUBSTRINGS:
        assert needle not in body, f"body leaked {needle!r}: {body[:500]}"


@pytest.fixture
def failing_database_probe():
    """Force the database probe to fail exactly as core/health reports it.

    ``HealthResult.error`` is the verbatim string core/health builds from the
    driver exception (``"<ClassName>: <text>"``) — the value that used to be
    published on the probe response. The session is stubbed so the failure
    comes from the probe delegate, not from a missing test database.
    """
    from src.app.core.health import HealthResult

    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    session_factory.return_value.__aexit__.return_value = False

    probe = AsyncMock(return_value=HealthResult(status="unhealthy", error=DB_LEAK))
    with (
        patch("src.app.core.db.database.local_session", new=session_factory),
        patch("src.app.core.health.check_database_health", new=probe),
    ):
        yield probe


@pytest.fixture
def failing_redis_probe():
    """Force the redis probe's own ``except`` branch to run."""
    redis_client = AsyncMock()
    redis_client.ping.side_effect = ConnectionRefusedError(REDIS_LEAK)
    with patch("src.app.core.redis_provider.get_redis", return_value=redis_client):
        yield redis_client


def test_health_probe_publishes_class_name_and_no_exception_text(
    client, failing_database_probe, failing_redis_probe
) -> None:
    """/api/health: the class name is published, every secret is absent.

    Status semantics are pinned too — a probe that swallowed failure to
    protect its output would be a worse defect than the leak.
    """
    with structlog.testing.capture_logs() as captured:
        response = client.get("/api/health", headers=TENANT_HEADERS)

    assert response.status_code == 200, response.text
    body = response.text
    payload = response.json()

    # The safe signal IS present — an operator still learns WHICH dependency broke.
    checks = payload["checks"]
    assert checks["database"]["status"] == "unhealthy"
    assert checks["database"]["error"] == "OperationalError"
    assert checks["redis"]["status"] == "unhealthy"
    assert checks["redis"]["error"] == "ConnectionRefusedError"

    # The unsafe signal is absent. These are the assertions that fail
    # against the pre-fix code.
    _assert_no_internals(body)

    # Failure is still reported as failure, not laundered into "ok".
    assert payload["status"] == "error"

    # The response models only the fields each probe actually measured: no
    # wall of nulls where the pre-model code emitted exactly the measured keys.
    for probe, check in checks.items():
        nulls = [field for field, value in check.items() if value is None]
        assert not nulls, f"probe {probe} padded with nulls: {nulls}"

    # The diagnostic is withheld, not destroyed: the full text reached the
    # log correlated by request_id, and the client already holds that id on
    # the X-Request-ID response header.
    probe_events = [rec for rec in captured if rec.get("event") == "health_probe_failed"]
    assert probe_events, "probe failure was not logged at all"
    logged_errors = {rec["probe"]: rec["error"] for rec in probe_events}
    assert logged_errors["database"] == DB_LEAK
    assert logged_errors["redis"] == REDIS_LEAK
    assert {rec["probe"] for rec in probe_events} >= {"database", "redis"}
    assert all(rec.get("request_id") for rec in probe_events), "log lines must carry request_id"

    assert failing_database_probe.await_count == 1
    assert failing_redis_probe.ping.await_count == 1


def test_health_probe_never_exposes_a_raw_exception_field(
    client, failing_database_probe, failing_redis_probe
) -> None:
    """No probe field may hold exception text, whatever it is named.

    Guards the whole ``checks`` subtree, not just ``error``: a future field
    named ``detail``/``message``/``error_detail`` would otherwise reintroduce
    the same leak without touching the assertions above.
    """
    response = client.get("/api/health", headers=TENANT_HEADERS)

    assert response.status_code == 200, response.text
    for probe, check in response.json()["checks"].items():
        for field, value in check.items():
            if isinstance(value, str):
                _assert_no_internals(f"{probe}.{field}={value}")
                assert field != "error_detail", f"probe {probe} grew a raw-error field"


def test_health_probe_without_tenant_header_is_400_and_leaks_nothing(client, failing_database_probe) -> None:
    """No ``X-Tenant-ID`` -> 400 before the probe runs, and the 400 is inert."""
    response = client.get("/api/health")

    assert response.status_code == 400, response.text
    _assert_no_internals(response.text)
    assert failing_database_probe.await_count == 0, "probe must not run on a rejected request"


def test_metrics_gives_superuser_the_full_exception_text(client) -> None:
    """COUNTER-TEST: redaction must not starve the operator.

    Redacting the probe without preserving the diagnostic on the
    superuser-gated route would just convert the outage into a blind one.
    """
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError(DB_LEAK)):
        response = client.get("/api/metrics", headers=TENANT_HEADERS)

    assert response.status_code == 500, response.text
    payload = response.json()
    assert payload["error_detail"] == DB_LEAK
    assert payload["error_type"] == "RuntimeError"
    # The specific diagnostic bits an operator needs, verbatim.
    assert "hunter2" in response.text
    assert "SELECT 1" in response.text
    assert "postgresql://" in response.text


def test_metrics_json_gives_superuser_the_full_exception_text(client) -> None:
    """/metrics/json mirrors /metrics: full text under ``error_detail``."""
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError(REDIS_LEAK)):
        response = client.get("/api/metrics/json", headers=TENANT_HEADERS)

    assert response.status_code == 500, response.text
    payload = response.json()
    assert payload["error_detail"] == REDIS_LEAK
    assert payload["error_type"] == "RuntimeError"


def test_metrics_reaches_superuser_on_the_tenant_exempt_scraper_path(client) -> None:
    """/api/metrics is tenant-exempt, so a scraper sends no X-Tenant-ID.

    This is the path a real Prometheus job takes. The superuser gate still
    applies, and the diagnostic must still arrive — the fix must not work
    only for callers that happen to include a tenant header.
    """
    with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError(DB_LEAK)):
        response = client.get("/api/metrics")

    assert response.status_code == 500, response.text
    payload = response.json()
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_detail"] == DB_LEAK


def test_metrics_is_403_for_non_superuser_and_leaks_nothing(client) -> None:
    """A non-superuser is refused, and the refusal carries no internals."""
    regular = MockUser(
        id=2,
        username="regular",
        email="regular@example.com",
        is_superuser=False,
        is_active=True,
    )
    with override_test_principal(regular, app=client.app):
        with patch("src.app.api.monitoring.get_r1_metrics", side_effect=RuntimeError(DB_LEAK)):
            response = client.get("/api/metrics", headers=TENANT_HEADERS)

    assert response.status_code == 403, response.text
    _assert_no_internals(response.text)
    assert "error_detail" not in response.text


def test_readiness_probe_publishes_statuses_not_driver_text(client) -> None:
    """/health/ready must not grow an error-text field either."""
    from src.app.core.health import HealthResult

    results = (
        HealthResult(status="unhealthy", error=DB_LEAK),
        HealthResult(status="healthy"),
        HealthResult(status="healthy"),
        True,
    )
    with patch(
        "src.app.api.monitoring._collect_readiness_results",
        new=AsyncMock(return_value=results),
    ):
        response = client.get("/api/health/ready", headers=TENANT_HEADERS)

    assert response.status_code == 503, response.text
    _assert_no_internals(response.text)
    assert response.json()["checks"]["database"] == "unhealthy"
