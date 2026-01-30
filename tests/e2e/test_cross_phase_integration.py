import asyncio

import pytest

from src.app.core.job_monitoring import get_job_monitor
from src.app.core.resilience.db_circuit_breaker import db_circuit
from src.app.core.utils.circuit_breaker import CircuitBreakerOpen, CircuitState


@pytest.fixture
def job_monitor():
    return get_job_monitor()


import time


@pytest.mark.asyncio
async def test_circuit_breaker_impact_on_job_monitoring(job_monitor, mock_redis):
    """
    Test Phase 5.1: Cross-phase integration.
    Verify that Database Circuit Breaker state changes are correctly handled
    and reflected in Job Monitoring metrics.
    """

    # Reset states
    db_circuit.reset()
    # Ensure fresh redis state for this test if needed (mock_redis is function scoped but let's be safe)
    mock_redis.store.clear()
    await job_monitor.clear_dlq()

    # --- Step 1: Normal Operation (Happy Path) ---
    job_id_success = "job_success_123"
    func_name = "process_data_task"

    # Simulate a successful job execution
    await job_monitor.record_job_start(job_id_success, func_name)
    # (Simulate DB operation success)
    await db_circuit.record_success()
    await job_monitor.record_job_success(job_id_success, func_name, duration=0.5)

    # Verify metrics
    metrics = await job_monitor.get_job_metrics()
    assert func_name in metrics
    assert metrics[func_name].get("success", 0) == 1
    assert metrics[func_name].get("failed", 0) == 0
    assert db_circuit.state == CircuitState.CLOSED

    # --- Step 2: Circuit Open (Failure Path) ---
    job_id_fail = "job_fail_456"

    # Force Circuit Breaker to OPEN state
    db_circuit._open()
    # Manually set last failure time to now to prevent immediate transition to HALF_OPEN
    # The state property checks (time - last_failure >= timeout) -> HALF_OPEN
    db_circuit._last_failure_time = time.time()
    assert db_circuit.state == CircuitState.OPEN

    await job_monitor.record_job_start(job_id_fail, func_name)

    # Simulate job failure due to Circuit Breaker
    try:
        # This is what execute_query_with_circuit_breaker would do
        # We manually raise it to simulate the integration without mocking the entire DB layer
        # as we are testing the INTERACTION between CB and Job Monitor
        recovery_in = db_circuit.recovery_timeout - (asyncio.get_running_loop().time() - db_circuit.last_failure_time)
        raise CircuitBreakerOpen(db_circuit.name, max(0, 10.0))
    except CircuitBreakerOpen as e:
        # Worker catches exception and records failure
        await job_monitor.record_job_failure(job_id_fail, func_name, str(e))

    # --- Step 3: Verification ---

    # Verify Job Monitor recorded the failure
    metrics = await job_monitor.get_job_metrics()
    assert metrics[func_name].get("failed", 0) == 1
    assert metrics[func_name].get("success", 0) == 1

    # Verify Dead Letter Queue contains the circuit breaker error
    dlq = await job_monitor.get_dead_letter_queue()
    assert len(dlq) == 1
    assert dlq[0]["job_id"] == job_id_fail
    assert "Circuit breaker 'database' is open" in dlq[0]["error"]

    # Verify Health Summary reflects the issue
    summary = await job_monitor.get_summary()
    assert summary["total_failures"] >= 1

    # Cleanup
    db_circuit.reset()
    await job_monitor.clear_dlq()
