"""S-8: count the debug events the attack-scan method gate emits, per level.

WHAT THIS MEASURES, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------------
The attack-detection middleware skips the body scan for every method outside
POST/PUT/PATCH and announces the skip with a DEBUG event::

    if method not in _BODY_METHODS:
        if logger.is_enabled_for(logging.DEBUG):
            logger.debug("attack_detection_skipped_body_scan_for_method", ...)

S-8's own arithmetic is that this "moves capacity, not p95": the guard and the
event it suppresses are 0 us of the 4.09 ms uncontended floor and 0.00 % of
the 740 ms p95. A latency figure therefore CANNOT substantiate that claim --
there is nothing to substantiate. What can be substantiated is a COUNT: how
many ``logger.debug`` invocations the call site makes on a bodyless GET, at
each effective level. A count is a structural property of the code and the
configuration, not a timing of a machine, so this harness reports COUNTS and
no durations at all, and its numbers are the same on any host.

THE ARMS
--------
Every arm drives the REAL middleware over the REAL ASGI interface with a
benign bodyless GET, and the arms differ ONLY in the effective level of the
module's stdlib logger:

    debug    the module logger at DEBUG  -> the predicate is True
    info     the module logger at INFO   -> the predicate is False
    default  the module logger at NOTSET -> the root's level decides

The invariant that makes the comparison mean something is that the GUARD is
evaluated the same number of times in EVERY arm. If it were not, a difference
in the debug count could be explained by the call site being reached a
different number of times rather than by the predicate's value. The harness
counts the guard's evaluations separately from the debug calls, REPORTS both,
and REFUSES to publish a run whose arms disagree on the evaluation count.

WHY ``setup_logging()`` RUNS FIRST
----------------------------------
``is_enabled_for`` only consults the effective stdlib level because A-3
(FastAPI_saas-49yx) configured structlog with this repository's own
``LevelAwareBoundLogger``. structlog's ambient default is
``BoundLoggerFilteringAtNotset``, whose predicate is structurally always True
-- so against that default this harness would report "the guard gates
nothing" in every arm and would be measuring structlog's default rather than
this repository's logging stack. The harness therefore installs the
repository's real configuration (``core/logger.py::setup_logging``) before
counting, and records the configuration it installed.

PROVENANCE
----------
The driver is IN THE TREE. S-8's inherited standing rule asked for "the
microbenchmark or round-trip counter that substantiates the capacity claim,
logged in full"; ic9r's close recorded that as NOT delivered ("need Linux
target + live run"). The counter is the part of that deliverable that does
not need a live run: it counts a structural property. The recorded artefact
is ``perf-results/RESULT-20261019-uo44/``.

Usage:
    uv run python src/benchmarks/s8_logger_call_counter.py
    uv run python src/benchmarks/s8_logger_call_counter.py --requests 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: The module whose logger the middleware's guard consults.
MODULE_LOGGER = "src.app.middleware.attack_detection"

#: A bodyless, benign GET. The path is not in the exempt set and carries no
#: attack pattern, so the request is scanned and found clean.
BODYLESS_PATH = "/api/v1/users/me"

DEFAULT_REQUESTS = 25

#: The three arms, as (name, module-logger level). ``None`` means NOTSET, i.e.
#: the module logger inherits whatever the root is configured at.
ARMS: tuple[tuple[str, int | None], ...] = (
    ("debug", logging.DEBUG),
    ("info", logging.INFO),
    ("default", None),
)

# Output goes to the repo's report directory, NOT to this file's own directory:
# src/benchmarks/ holds source, and an artefact written beside it would be
# mistaken for a checked-in baseline.
OUT_DIR = ROOT / "reports"


def bootstrap() -> None:
    """Put the process where the harness needs it, and pin the log level.

    Call this BEFORE importing anything under src.app: the level below is read
    at settings-construction time, and a module-level ``load_dotenv(override=
    True)`` would replace the environment a pytest session runs under.
    """
    os.chdir(ROOT)
    load_dotenv(ROOT / "src" / ".env", override=True)
    os.environ.setdefault("ENVIRONMENT", "local")
    # The "default" arm is only meaningful against a KNOWN root level, so the
    # level is pinned rather than inherited, and the value that took effect is
    # recorded in the artefact.
    os.environ["LOG_LEVEL"] = "INFO"


class _StubIpRules:
    """The ``IpRules`` seam, stubbed.

    The rules are consulted only when an attack is DETECTED; these requests are
    benign, so the stub is never asked. It exists so the counter has no Redis
    dependency and cannot be silently degraded by an unreachable Redis.
    """

    async def check_ip_status(self, ip_address: str) -> tuple[bool, bool]:
        return (False, False)

    async def ban_ip(
        self,
        ip_address: str,
        reason: str,
        expires_at: datetime | None = None,
    ) -> bool:
        return False


async def _downstream(scope: Any, receive: Any, send: Any) -> None:
    """The stub application the middleware forwards to."""

    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _sent(message: Any) -> None:
    """Collect the downstream response. The counter does not read it."""


async def _receive() -> dict[str, Any]:
    """A bodyless request channel: one empty terminal frame."""
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope() -> dict[str, Any]:
    """A native-ASGI HTTP scope for a bodyless GET."""
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": BODYLESS_PATH,
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "state": {},
    }


async def count_arm(*, name: str, module_level: int | None, requests: int) -> dict[str, Any]:
    """Drive ``requests`` bodyless GETs at one effective level, and count.

    Two counters are collected:

    ``guard_evaluations``
        how many times the middleware's guard asked the logging stack whether
        DEBUG was wanted. Counted on the bound logger's own predicate, so it is
        the GUARD that is counted and not the stdlib's internal level check
        that follows an emitted record.

    ``debug_calls``
        how many times the call site actually invoked ``logger.debug``.

    The two are counted separately on purpose: the claim under test is that
    the guard is EVALUATED every time and the log call is made only when the
    predicate allows it. A single counter could not tell "the guard closed"
    from "the call site was never reached".
    """
    import src.app.middleware.attack_detection as ad
    from src.app.core.logger import LevelAwareBoundLogger
    from src.app.middleware.attack_detection import AttackDetectionMiddleware

    module_logger = logging.getLogger(MODULE_LOGGER)
    saved_level = module_logger.level
    module_logger.setLevel(logging.NOTSET if module_level is None else module_level)
    try:
        evaluations: list[int] = []
        debug_calls: list[str] = []

        real_is_enabled_for = LevelAwareBoundLogger.is_enabled_for
        real_debug = ad.logger.debug

        def counting_is_enabled_for(self: Any, target_level: int) -> bool:
            if getattr(getattr(self, "_logger", None), "name", None) == MODULE_LOGGER:
                evaluations.append(target_level)
            return real_is_enabled_for(self, target_level)

        def counting_debug(event: str, *args: Any, **kwargs: Any) -> Any:
            debug_calls.append(event)
            return real_debug(event, *args, **kwargs)

        middleware = AttackDetectionMiddleware(app=_downstream, ip_rules=_StubIpRules())
        predicate_value = bool(ad.logger.is_enabled_for(logging.DEBUG))
        effective_level = module_logger.getEffectiveLevel()

        with (
            patch.object(LevelAwareBoundLogger, "is_enabled_for", counting_is_enabled_for),
            patch.object(ad.logger, "debug", side_effect=counting_debug),
        ):
            for _ in range(requests):
                await middleware(_scope(), _receive, _sent)

        return {
            "arm": name,
            "requests": requests,
            "module_logger_level": logging.getLevelName(module_logger.level),
            "effective_level": logging.getLevelName(effective_level),
            "predicate_value": predicate_value,
            "guard_evaluations": len(evaluations),
            "guard_evaluation_levels": sorted({logging.getLevelName(level) for level in evaluations}),
            "debug_calls": len(debug_calls),
            "debug_events": sorted(set(debug_calls)),
        }
    finally:
        module_logger.setLevel(saved_level)


def check_arms(arms: dict[str, dict[str, Any]], *, requests: int) -> None:
    """Refuse a run whose arms do not say what the counter claims to measure.

    Every check here is a REFUSAL, not a report: a counter that cannot show
    the call site being reached the same number of times in every arm, or
    whose predicate does not take the value the level says it should, has
    measured something other than what it claims, and publishing its numbers
    would make that invisible.
    """
    expected_evaluations = dict.fromkeys(arms, requests)
    for name, arm in arms.items():
        if arm["guard_evaluations"] != expected_evaluations[name]:
            raise AssertionError(
                f"arm {name!r} evaluated the guard {arm['guard_evaluations']} time(s), expected "
                f"{requests}. The call site was not reached once per request, so the debug-call "
                f"count in this arm cannot be compared with the others."
            )
        expected_debug_calls = requests if arm["predicate_value"] else 0
        if arm["debug_calls"] != expected_debug_calls:
            raise AssertionError(
                f"arm {name!r} made {arm['debug_calls']} logger.debug call(s) with a predicate of "
                f"{arm['predicate_value']}; expected {expected_debug_calls}. The counter is not "
                f"measuring the call site's behaviour."
            )
    if arms["debug"]["predicate_value"] is not True:
        raise AssertionError("the debug arm's predicate was not True, so the DEBUG level is not being exercised")
    if arms["info"]["predicate_value"] is not False:
        raise AssertionError("the info arm's predicate was not False, so the guard is not closing above DEBUG")


def build_artefact(arms: dict[str, dict[str, Any]], *, requests: int, root_level: int) -> dict[str, Any]:
    """Assemble the recorded artefact."""
    return {
        "schema": "s8_logger_call_counter/1",
        "bead": "FastAPI_saas-uo44",
        "item": "S-8",
        "title": (
            "The attack-scan method gate evaluates the predicate on every bodyless request "
            "and emits the skip event only above DEBUG"
        ),
        "quantity": (
            "COUNTS of guard evaluations and logger.debug invocations. This artefact makes NO "
            "latency claim: S-8's own arithmetic is 0 us, 0.00 % of the 4.09 ms uncontended floor "
            "and 0.00 % of the 740 ms p95, so there is no duration to report."
        ),
        "method": {
            "driver": "src/benchmarks/s8_logger_call_counter.py",
            "in_repo": True,
            "request": "one bodyless, benign GET through the real AttackDetectionMiddleware ASGI __call__",
            "path": BODYLESS_PATH,
            "arms": "the module's stdlib logger at DEBUG, at INFO, and at NOTSET (inheriting the root)",
            "guard_counter": (
                "the bound logger's own is_enabled_for, filtered to the module logger, so the GUARD "
                "is counted and not the stdlib's internal level check"
            ),
            "debug_counter": "the module proxy's own debug method, so the CALL SITE is counted",
            "logging_configuration": "src/app/core/logger.py::setup_logging, the repository's real one",
            "ip_rules": "stubbed; the rules are consulted only when an attack is detected, and none is",
        },
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "host_relevant": False,
            "host_relevant_why": (
                "the quantity is a COUNT of calls, not a duration: the same code and the same "
                "configuration produce the same counts on any host"
            ),
        },
        "configuration": {
            "root_level": logging.getLevelName(root_level),
            "module_logger": MODULE_LOGGER,
            "log_level_env": os.environ.get("LOG_LEVEL"),
        },
        "requests_per_arm": requests,
        "arms": arms,
        "verdict": {
            "statement": (
                "The guard is evaluated once per bodyless request in EVERY arm, and the skip event "
                "is emitted only in the arm whose level permits it: one logger.debug call per "
                "request at DEBUG, none at INFO and above."
            ),
            "guard_evaluations_per_arm": {name: arm["guard_evaluations"] for name, arm in arms.items()},
            "debug_calls_per_arm": {name: arm["debug_calls"] for name, arm in arms.items()},
            "predicate_per_arm": {name: arm["predicate_value"] for name, arm in arms.items()},
        },
        "limits": [
            "This is a COUNT, not a latency measurement, and it substantiates a structural "
            "property of the call site rather than a performance figure. S-8's own arithmetic "
            "stands: 0 us, 0.00 % of the 4.09 ms uncontended floor, 0.00 % of the 740 ms p95.",
            "The requests are driven in-process through the middleware's ASGI __call__ with a stub "
            "downstream application, so the counter exercises the middleware and not the whole "
            "application stack. The END-TO-END-over-HTTP leg is a separate deliverable.",
            "The middleware's own body scan and detection paths are not exercised: the requests "
            "are benign by construction, which is what makes the skip the only event in play.",
            "The counter runs against this repository's logging configuration, installed by "
            "setup_logging(). Against structlog's ambient default the predicate is structurally "
            "always True and both arms would report a debug call per request.",
        ],
    }


async def run(requests: int) -> dict[str, Any]:
    """Install the repository's logging configuration, then count each arm."""
    from src.app.core.logger import setup_logging, shutdown_logging

    setup_logging()
    try:
        root_level = logging.getLogger().getEffectiveLevel()
        arms: dict[str, dict[str, Any]] = {}
        for name, module_level in ARMS:
            arms[name] = await count_arm(name=name, module_level=module_level, requests=requests)
    finally:
        shutdown_logging()

    check_arms(arms, requests=requests)
    return build_artefact(arms, requests=requests, root_level=root_level)


def cli() -> None:
    """Entry point. bootstrap() runs FIRST, before anything under src.app."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS, help="bodyless GETs per arm")
    parser.add_argument(
        "--output",
        type=Path,
        default=OUT_DIR / "s8_logger_call_counter.json",
        help="where the artefact is written",
    )
    args = parser.parse_args()

    bootstrap()
    artefact = asyncio.run(run(args.requests))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artefact, indent=2, default=str), encoding="utf-8")
    print(f"wrote {args.output}")
    for name, arm in artefact["arms"].items():
        print(
            f"  {name:>7}: guard_evaluations={arm['guard_evaluations']} "
            f"debug_calls={arm['debug_calls']} predicate={arm['predicate_value']} "
            f"effective_level={arm['effective_level']}"
        )


if __name__ == "__main__":
    cli()
