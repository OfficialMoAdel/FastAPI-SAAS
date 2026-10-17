"""nrgi.3: measured cost of the attack-detection body scan (docs/adr/0008).

This is the measurement the bead asked for, kept as a runnable benchmark so
the numbers stay checkable. The DECISION it supports — do not bound the scan
further — is recorded in docs/adr/0008; these values are the evidence.

Measured on CPython 3.13.7 / Windows / .venv, reusing the harness helpers in
test_r5_micros.py (warmup, gc.collect() between batches, min-of-N):

    _scan_request, bodyless GET ....................   6.7 us  [did not reproduce]
    _scan_request, POST 77 B JSON ..................  47.8 us
    _scan_request, POST 60 KB clean (prefilter miss)  1087 us
    _scan_request, POST 40 KB benign 'or' (hit) .... 16015 us
    _scan_request, POST 128 KB benign 'or' (hit) ... 49621 us

    full request, bodyless GET .......... p50 3546  p90 5121  p99  6950 us
    full request, POST 77 B JSON ....... p50 4807  p90 6686  p99  8959 us
    full request, POST 40 KB HIT ....... p50 92124 p90 161606 p99 214040 us

Two conclusions this file pins:

1. **A bodyless request is not the cost.** GET costs almost nothing because the
   body scan is already method-gated (attack_detection.py, ``_BODY_METHODS``).
   The DEBUG log on that skip was documented as needing its guard because an
   unguarded structlog ``debug()`` was measured at 44.6 us -- 6.7x the 6.7 us
   scan it announces. Both figures did not reproduce, and the premise is
   false: ``logger.is_enabled_for(logging.DEBUG)`` on this repo's structlog
   stack returns ``True`` whatever the stdlib root level is, so the guard gates
   nothing and the log line is emitted on every bodyless GET.
   ``scripts/s7_scan_cost_probe.py`` re-measures the shape over HTTP; see
   docs/adr/0008 and the comment at that call site. The conclusion survives
   because the scan is method-gated, not because of any microsecond constant.
2. **The cost is the prefilter literal, not the method gate.** ``b"or"`` is a
   necessary literal for ``sql_or`` but is also a substring of color/for/
   order, so ordinary JSON reaches the full 17-group regex.

Per test_r5_micros.py convention the timings are log-only (CI variance is
high); the assertions below are load-robust structural tripwires that hold on
any machine.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.app.middleware.attack_detection import (
    COMBINED_ATTACK_RE,
    AttackDetectionMiddleware,
    _attack_prefilter_hit,
)
from src.benchmarks import protocol
from tests.benchmarks.test_r5_micros import best_of_benchmark, run_benchmark

logger = logging.getLogger(__name__)

SQLI = b"UNION SELECT password FROM users"

# A benign body that trips the prefilter: "or" occurs inside color/for.
BENIGN_40K = b'{"color":"blue","sort":"for","items":"' + b"x" * (40 * 1024) + b'"}'
BENIGN_128K = b'{"color":"blue","sort":"for","items":"' + b"x" * (120 * 1024) + b'"}'
CLEAN_60K = b'{"items":"' + b"x" * (60 * 1024) + b'"}'


def _scope(method: str, body: bytes, query: bytes = b""):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/api/v1/files",
        "query_string": query,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if body
        else [(b"accept", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
        "state": {},
    }
    chunks = [body] if body else []

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return scope, receive


@pytest.fixture
def mw() -> AttackDetectionMiddleware:
    return AttackDetectionMiddleware(app=MagicMock())


async def _scan_cost(
    mw: AttackDetectionMiddleware, method: str, body: bytes, query: bytes, n: int, name: str
) -> protocol.MicroResult:
    """Us/call for _scan_request on fresh receive channels, per H-11.

    Previously this took a hand-rolled 20-call warm-up, one ``gc.collect()``,
    and reported the MEAN of ``n`` calls. The mean is the wrong estimator here:
    one GC pause or scheduler preemption inside the loop drags the whole
    number up, and there is no way to tell that apart from a real regression.
    The protocol takes the best of several batches instead, so a single slow
    call cannot become the headline.
    """
    calls = 0

    async def probe() -> None:
        nonlocal calls
        calls += 1
        scope = _scope(method, body, query)
        await AttackDetectionMiddleware._scan_request(
            mw, scope[0]["path"], scope[0]["query_string"], scope[0]["headers"], scope[0]["method"], scope[1]
        )

    result = await protocol.measure_async(probe, name=name, iterations=n, warmup=20)
    assert calls == 20 + n * result.repetitions
    return result


class TestScanCostBench:
    """The per-request cost the ADR quotes. Log-only (see module docstring)."""

    async def test_bodyless_get_is_cheap(self, mw: AttackDetectionMiddleware) -> None:
        """The bead's core question: a bodyless request must not pay for the scan."""
        result = await _scan_cost(mw, "GET", b"", b"page=1&limit=50", 2000, "scan_get_bodyless")
        logger.info("nrgi.3 scan GET bodyless: %s", protocol.render(result))
        # Load-robust tripwire, not a timing gate: a GET that drained a body
        # would be orders of magnitude slower than one that does not.
        protocol.verdict(result, protocol.Ceiling(2_000.0, "scan_get_bodyless")).assert_passed()

    async def test_typical_post(self, mw: AttackDetectionMiddleware) -> None:
        result = await _scan_cost(
            mw, "POST", b'{"email":"user@example.com","password":"hunter2!!"}', b"", 2000, "scan_post_77b"
        )
        logger.info("nrgi.3 scan POST 77B: %s", protocol.render(result))
        protocol.verdict(result, protocol.Ceiling(5_000.0, "scan_post_77b")).assert_passed()

    async def test_clean_large_body(self, mw: AttackDetectionMiddleware) -> None:
        result = await _scan_cost(mw, "POST", CLEAN_60K, b"", 200, "scan_post_60k_clean")
        logger.info("nrgi.3 scan POST 60KB clean (prefilter miss): %s", protocol.render(result))
        protocol.verdict(result, protocol.Ceiling(50_000.0, "scan_post_60k_clean")).assert_passed()

    async def test_prefilter_hit_large_body_is_the_real_cost(
        self, mw: AttackDetectionMiddleware
    ) -> None:
        """The cost driver the ADR reports. Log-only; the bound is loose."""
        result = await _scan_cost(mw, "POST", BENIGN_40K, b"", 50, "scan_post_40k_prefilter_hit")
        logger.info("nrgi.3 scan POST 40KB benign 'or' (prefilter HIT): %s", protocol.render(result))

    async def test_scan_cap_size_benign_hit(self, mw: AttackDetectionMiddleware) -> None:
        result = await _scan_cost(mw, "POST", BENIGN_128K, b"", 10, "scan_post_128k_prefilter_hit")
        logger.info("nrgi.3 scan POST 128KB benign 'or' (prefilter HIT): %s", protocol.render(result))


class TestPrefilterCostBench:
    """Where the regex time actually goes. Log-only, per convention."""

    def test_regex_only_on_a_prefilter_hit(self) -> None:
        us, ops = run_benchmark(
            "regex_128k_benign_hit",
            lambda: list(COMBINED_ATTACK_RE.finditer(BENIGN_128K)),
            iterations=10,
            warmup=2,
        )
        logger.info("nrgi.3 regex(128KB hit): %.2fus/op (%.0f ops/sec)", us, ops)
        assert ops > 0

    def test_regex_on_a_prefilter_miss_body(self) -> None:
        us, ops = run_benchmark(
            "regex_128k_clean",
            lambda: list(COMBINED_ATTACK_RE.finditer(b"x" * (128 * 1024))),
            iterations=200,
            warmup=10,
        )
        logger.info("nrgi.3 regex(128KB clean): %.2fus/op (%.0f ops/sec)", us, ops)


class TestDebugLogCost:
    """Why the skip log is guarded — the number that decides the design."""

    def test_guarded_debug_beats_unguarded(self) -> None:
        """A bare debug() is more expensive than the scan it would announce.

        This is the one place in the file that compares two arms of the same
        run, and H-11 bans ``new < old`` because it passes on noise. The fix is
        not to delete the comparison - the ORDERING is the whole point here,
        and the absolute values are machine-specific - but to require the
        ordering to hold by MORE than the measured noise floor
        (protocol.NOISE_FLOOR_RATIO). A bare ``guarded < bare`` would be
        satisfied by two numbers 1% apart, which is exactly the failure this
        programme exists to end.
        """
        import logging

        from src.app.middleware import attack_detection as ad

        guarded_us = best_of_benchmark(
            "nrgi.3_is_enabled_for", lambda: ad.logger.is_enabled_for(logging.DEBUG), iterations=5000, warmup=500
        )
        bare_us = best_of_benchmark(
            "nrgi.3_bare_debug", lambda: ad.logger.debug("nrgi3_probe", method="GET"), iterations=2000, warmup=500
        )
        logger.info("nrgi.3 debug log cost: guarded=%.2fus bare=%.2fus", guarded_us, bare_us)
        ratio = bare_us / guarded_us
        assert ratio >= protocol.NOISE_FLOOR_RATIO, (
            f"guarding the debug log ({guarded_us:.2f}us) is only {ratio:.2f}x cheaper than "
            f"calling it ({bare_us:.2f}us). That gap is inside the {protocol.NOISE_FLOOR_RATIO}x "
            f"noise floor (perf C11 measured 7.1x run-to-run on identical code), so it is not "
            f"evidence that the guard pays for itself."
        )


class TestPrefilterLiteralBreadth:
    """The structural fact behind the ADR's open question (not a timing test)."""

    @pytest.mark.parametrize(
        "body",
        [
            b'{"email":"user@example.com","password":"hunter2!!"}',
            b'{"color":"orange","session":"none","word":"work"}',
            b'{"order":[{"product":"For","vendor":"Orion"}],"page":2}',
            b'{"query":"performance","sort":"order"}',
        ],
    )
    def test_ordinary_json_trips_the_prefilter(self, body: bytes) -> None:
        """``b"or"`` is a necessary literal for sql_or but matches ordinary words.

        These are benign bodies with NO attack: the prefilter says "maybe",
        the regex says "no", and the full regex has been paid for. This is
        the reason the scan is expensive on real traffic — recorded, and
        deliberately NOT changed here: narrowing it is a security decision
        about what reaches the regex, which belongs to the threat-model
        owner (see docs/adr/0008, "Open").
        """
        assert _attack_prefilter_hit(body) is True, "fixture no longer trips the prefilter"
        assert [m.lastgroup for m in COMBINED_ATTACK_RE.finditer(body)] == [], "fixture must stay benign"

    def test_deep_payload_is_still_a_true_positive(self) -> None:
        """Guard against 'fixing' the breadth by narrowing the literal unsoundly."""
        assert _attack_prefilter_hit(SQLI) is True
        assert [m.lastgroup for m in COMBINED_ATTACK_RE.finditer(SQLI)] == ["sql_union"]
