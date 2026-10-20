"""A-8: the body-scan process-pool offload.

ADR-0016's fail direction is FAIL-TO-CURRENT WITH A CIRCUIT BREAKER, and this
oracle pins all three parts of it:

- the offloaded scan returns EXACTLY the findings the in-process scan returns
  -- the same list, the same order, the same content. The offload is a
  capacity lever, never a detection lever;
- a pool-side failure falls back to the in-process scan AND increments the
  failure counter, so no request is left unscanned and none is rejected
  because the pool failed;
- the circuit breaker opens after more than five failures inside sixty
  seconds, and the offload is then not used at all.

NO TEST MAY BE WEAKENED TO ACCOMMODATE A CHANGE; NO ASSERTION MAY BE RELAXED TO
MAKE A NUMBER PASS. No number is asserted here at all: A-8's p95 was never
measured on the Linux target (L-13's parity figures are for the THREAD arm),
so this oracle judges BEHAVIOUR only and quotes no duration.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import pytest

from src.app.core.monitoring.r1_unified_metrics import get_r1_metrics, record_attack_scan_pool_failure
from src.app.middleware import attack_detection as ad

REPO_ROOT = Path(__file__).resolve().parents[3]
POOL_FAILURE_COUNTER = "r1_attack_scan_pool_failure_total"

# Payloads that exercise the scan: an attack, a clean body, a body that
# carries an attack in a non-ASCII payload, and one that trips the
# multipart-boundary mitigation.
CORPUS: tuple[bytes, ...] = (
    b"id=1&name=alice",
    b"username=admin'--",
    b"q=1 UNION SELECT * FROM users",
    b"body=hello; id",
    b"payload=\xe4\xb8\xad\xe6\x96\x87",
    b"x=<script>alert(1)</script>",
    b"path=../../etc/passwd",
)


def _middleware() -> ad.AttackDetectionMiddleware:
    """A middleware instance with the offload OFF (the shipped default)."""
    instance = ad.AttackDetectionMiddleware(app=object())  # type: ignore[arg-type]
    assert instance._offload is None
    return instance


class _BrokenExecutor:
    """An executor whose every submission fails, the way a dead pool does."""

    def __init__(self) -> None:
        self.submissions = 0

    def submit(self, *args: Any, **kwargs: Any) -> Any:
        self.submissions += 1
        raise BrokenProcessPool("the pool is dead")

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


class TestTheWorkerFindsExactlyWhatTheInProcessScanFinds:
    def test_the_worker_function_returns_the_same_findings_in_process(self) -> None:
        for payload in CORPUS:
            in_process = ad.scan_content_for_attacks(payload, "request_body")
            through_the_worker = ad._scan_in_worker((payload, "request_body", False))
            assert through_the_worker == in_process

    def test_the_worker_function_returns_the_same_findings_with_the_full_log(self) -> None:
        payload = b"username=admin'--"
        in_process = ad.scan_content_for_attacks(payload, "request_body", True)
        through_the_worker = ad._scan_in_worker((payload, "request_body", True))
        assert through_the_worker == in_process

    @pytest.mark.skipif(
        multiprocessing.get_start_method(allow_none=False) == "spawn",
        reason=(
            "the default start method here is spawn, and a spawned child re-imports the "
            "parent's __main__; under a console-script launcher that child cannot start, "
            "so the real pool cannot be exercised on this platform"
        ),
    )
    def test_a_real_worker_process_never_returns_a_different_answer(self) -> None:
        """The process arm, in a REAL pool.

        The property asserted is not "the pool worked" but "the pool can never
        return a DIFFERENT answer": the offload either produces exactly the
        in-process findings, or it fails and the fail direction takes over.
        A wrong answer is the only unacceptable outcome, and this is the
        property that must hold on every platform.

        On a platform whose default start method is ``spawn`` the pool may
        not even start under a console-script launcher (the spawned child
        re-imports the parent's ``__main__``), and the fail direction covers
        that. The failure is then counted, which the second assertion pins.
        """
        baseline = get_r1_metrics().get_counter(POOL_FAILURE_COUNTER)
        offload = ad._BodyScanOffload()
        try:
            payload = b"username=admin'--"
            findings = asyncio.run(offload.scan(payload, "request_body"))
            if findings is None:
                assert get_r1_metrics().get_counter(POOL_FAILURE_COUNTER) == baseline + 1
            else:
                assert findings == ad.scan_content_for_attacks(payload, "request_body")
        finally:
            offload.shutdown()

    def test_the_worker_runs_in_a_separate_interpreter_and_agrees(self) -> None:
        """The worker crosses a PROCESS boundary, in a fresh interpreter.

        A worker that only works in-process proves nothing about a process
        pool. This runs the worker in its own interpreter -- whose ``__main__``
        is ``-c``, so there is no console-script launcher to re-import -- and
        compares its answer with the in-process one.
        """
        payload = b"username=admin'--"
        expected = ad.scan_content_for_attacks(payload, "request_body")
        script = (
            "import json;"
            "from src.app.middleware.attack_detection import _scan_in_worker;"
            "print('__A8_JSON__' + json.dumps(_scan_in_worker((b\"username=admin'--\", 'request_body', False))))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
        stdout = completed.stdout.decode("utf-8", errors="replace")
        marker = stdout.rindex("__A8_JSON__") + len("__A8_JSON__")
        assert json.loads(stdout[marker:]) == expected


class TestTheMiddlewareOffloadedPathFindsExactlyWhatTheInProcessPathFinds:
    @pytest.mark.asyncio
    async def test_the_two_paths_produce_the_same_findings(self) -> None:
        in_process_middleware = _middleware()
        offloaded_middleware = _middleware()
        offloaded_middleware._offload = ad._BodyScanOffload(executor=ThreadPoolExecutor(max_workers=1))

        for payload in CORPUS:
            in_process: list[dict] = []
            await in_process_middleware._scan_body(payload, in_process)
            offloaded: list[dict] = []
            await offloaded_middleware._scan_body(payload, offloaded)
            assert offloaded == in_process

    @pytest.mark.asyncio
    async def test_the_offloaded_path_is_not_used_when_the_offload_is_off(self) -> None:
        middleware = _middleware()
        assert middleware._offload is None
        findings: list[dict] = []
        await middleware._scan_body(b"username=admin'--", findings)
        assert findings == ad.scan_content_for_attacks(b"username=admin'--", "request_body")


class TestTheFailDirectionIsFailToCurrent:
    @pytest.mark.asyncio
    async def test_a_pool_failure_falls_back_to_the_in_process_scan(self) -> None:
        offload = ad._BodyScanOffload(executor=_BrokenExecutor())  # type: ignore[arg-type]
        middleware = _middleware()
        middleware._offload = offload

        payload = b"username=admin'--"
        findings = await offload.scan(payload, "request_body")
        assert findings is None

        detected: list[dict] = []
        await middleware._scan_body(payload, detected)
        assert detected == ad.scan_content_for_attacks(payload, "request_body")

    @pytest.mark.asyncio
    async def test_a_pool_failure_is_counted(self) -> None:
        baseline = get_r1_metrics().get_counter(POOL_FAILURE_COUNTER)
        offload = ad._BodyScanOffload(executor=_BrokenExecutor())  # type: ignore[arg-type]
        assert await offload.scan(b"id=1", "request_body") is None
        assert get_r1_metrics().get_counter(POOL_FAILURE_COUNTER) == baseline + 1

    def test_the_failure_counter_is_registered(self) -> None:
        """A counter that is not registered is a silent no-op."""
        baseline = get_r1_metrics().get_counter(POOL_FAILURE_COUNTER)
        record_attack_scan_pool_failure()
        assert get_r1_metrics().get_counter(POOL_FAILURE_COUNTER) == baseline + 1


class TestTheCircuitBreaker:
    @pytest.mark.asyncio
    async def test_the_breaker_opens_after_more_than_five_failures(self) -> None:
        offload = ad._BodyScanOffload(executor=_BrokenExecutor())  # type: ignore[arg-type]
        for _ in range(ad._BodyScanOffload._FAILURE_THRESHOLD + 1):
            assert await offload.scan(b"id=1", "request_body") is None
        assert offload.breaker_open is True

    @pytest.mark.asyncio
    async def test_the_open_breaker_stops_the_offload_being_used(self) -> None:
        broken = _BrokenExecutor()
        offload = ad._BodyScanOffload(executor=broken)  # type: ignore[arg-type]
        for _ in range(ad._BodyScanOffload._FAILURE_THRESHOLD + 1):
            await offload.scan(b"id=1", "request_body")
        submissions_after_opening = broken.submissions
        assert await offload.scan(b"id=1", "request_body") is None
        assert broken.submissions == submissions_after_opening

    @pytest.mark.asyncio
    async def test_the_breaker_does_not_open_inside_the_window(self) -> None:
        offload = ad._BodyScanOffload(executor=_BrokenExecutor())  # type: ignore[arg-type]
        for _ in range(ad._BodyScanOffload._FAILURE_THRESHOLD):
            await offload.scan(b"id=1", "request_body")
        assert offload.breaker_open is False
