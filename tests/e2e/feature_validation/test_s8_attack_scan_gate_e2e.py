"""S-8 end-to-end: the attack-scan method gate exercises the REAL logging predicate.

WHAT THIS SCRIPT DOES
---------------------
It drives the REAL application -- the real middleware stack, the real logging
stack -- over the real ASGI interface, and asserts that the guard the
middleware actually executes behaves the way the guard is documented to
behave. Nothing here is mocked, because the point of S-8 is that the previous
tests could not fail: they were de-mocked against a world in which the
predicate was structurally always True.

Before A-3 (``FastAPI_saas-49yx``) ``structlog.configure`` set no
``wrapper_class``, so the bound logger was ``BoundLoggerFilteringAtNotset`` and
``is_enabled_for(logging.DEBUG)`` returned True whatever the stdlib root level
was. The predicate genuinely gated nothing and the debug call WAS made on
every bodyless GET. The unit test
(``tests/unit/middleware/test_attack_scan_method_gate.py``) was de-mocked
against that world, so it asserted the SHAPE of a guard rather than its
behaviour.

THE ASSERTIONS
--------------
(a) The predicate is False at INFO and True at DEBUG, asserted as an EQUALITY
    (``is False`` / ``is True``). A truthiness assertion would also be
    satisfied by a predicate that is None, and "is True" alone would be
    satisfied by the always-True default this exercise exists to rule out.
(b) A GET with no body does NOT enter the body-scan gate, and a POST with a
    body DOES. Proved from both sides: the bodyless GET emits the skip event
    at DEBUG and is served, while the POST's body is scanned -- a body
    carrying a SQLi payload is detected with ``location == "request_body"``
    and the request is rejected, and a benign body is scanned and passes.
(c) The tripping user-agent still trips, and the recorded reason is the
    POSITION of the command, not the absence of a substring. The audit
    correction is: ``Pixel`` contains no ``id``; ``Android`` DOES
    (A-n-d-r-o-i-d). The ``id`` inside ``Android`` is mid-word, so it never
    sits at ``;`` + command position and the verdict is benign. A test that
    asserted "the UA is clean because Pixel has no id" would pass for the
    wrong reason and would be broken incorrectly the moment someone fixed the
    attribution. This script therefore drives two user-agents that BOTH carry
    the substring ``id`` and shows that only the one with the command in
    command position trips.
(d) HARD GATE: A-3 has landed. The script installs this repository's real
    logging configuration and asserts the bound logger is
    ``LevelAwareBoundLogger``. Without it the predicate is structurally always
    True, every assertion above would be vacuous, and the script would be
    "proving" the pre-A-3 behaviour.

WHAT IS NOT ASSERTED
--------------------
No latency is asserted. S-8's own arithmetic is 0 us -- 0.00 % of the 4.09 ms
uncontended floor and 0.00 % of the 740 ms p95 -- so there is no duration to
substantiate: this moves capacity, not p95.

The client-observed clock and the server-observed clock are RECORDED per
request. The server-side ``X-Response-Time`` header is written only when
``REQUEST_TIMER_ENABLED`` was set when the application was CONSTRUCTED (the
flag is read once, at import of the guard-ordering module), so in a session
that does not set it the header is absent; the script records the reason for
its absence rather than papering over it, and the recorded artefact says
which mode the run was in. No threshold is applied to either clock.

The recorded artefact is ``perf-results/RESULT-20261019-xfpt/`` and the last
test in this file proves the recorded facts are the facts this run measured,
so the artefact is a record rather than a story.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog

import src.app.middleware.attack_detection as ad
from src.app.core.logger import LevelAwareBoundLogger, setup_logging, shutdown_logging

pytestmark = pytest.mark.e2e

#: The stdlib logger the middleware's guard delegates to.
MODULE_LOGGER = "src.app.middleware.attack_detection"

#: The event the guard emits when it skips the body scan.
SKIP_EVENT = "attack_detection_skipped_body_scan_for_method"

#: The event the middleware emits for every detected attack.
DETECTED_EVENT = "attack_detected"

#: The bodyless route used for the GET legs. It is not in the exempt set, so
#: the middleware really runs on it.
BODYLESS_PATH = "/api/v1/users/me"

#: The body-carrying route used for the POST legs. It is authenticated and
#: self-scoped, which is why the POST legs authenticate.
POST_PATH_TEMPLATE = "/api/v1/posts/by-user/{username}"

#: A bodyless, benign request. It must be free of every attack pattern, or the
#: detector would reject it and the test would be measuring the wrong thing.
BENIGN_BODY = {"title": "S-8 gate check", "text": "This is the content of my post."}

#: A body that carries a SQLi payload. ``sql_union`` is
#: ``\\bunion\\b.*?\\bselect\\b``, so this matches it and the request is
#: rejected with the detection located in the BODY.
SQLI_BODY = {
    "title": "S-8 gate check",
    "text": "harmless prefix UNION SELECT password FROM users harmless suffix",
}

#: The user-agent that must NOT trip. It carries ";" (so the necessary-literal
#: prefilter admits it) and it carries the substring "id" inside "Android" --
#: but the "id" is mid-word, never at ";" + command position, so the full
#: regex clears it. This is the corrected attribution: "Pixel" has no "id";
#: "Android" does, and its POSITION is what makes the verdict benign.
PIXEL_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
)

#: The user-agent that MUST trip. It carries the SAME substring "id", in a
#: different position: directly after a ";" separator, which is the command
#: position ``cmd_inj`` matches. The only difference between this and the
#: benign user-agent is where the command sits.
COMMAND_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ; id"

#: The command-injection pattern, as the middleware compiles it.
COMMAND_PATTERN = r"(?:;|\|\||&&)\s*(?:cat|ls|id|whoami|ping|curl|wget)\b"

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTEFACT_PATH = REPO_ROOT / "perf-results" / "RESULT-20261019-xfpt" / "s8_attack_scan_gate.json"

#: The env var that tells the script to write the recorded artefact. The
#: write is operator-driven, not a side effect of every test run.
WRITE_ARTEFACT_ENV = "S8_E2E_WRITE_ARTEFACT"

#: The probe runs ONCE per session -- it drives real requests and creates a
#: post -- and every assertion reads the same recorded run. A module-level
#: cache is used because the fixtures it depends on are function-scoped.
_PROBE: list[dict[str, Any]] | None = None


class _RecordCapture(logging.Handler):
    """Capture the REAL log records the middleware emits.

    The records are observed on the module's own stdlib logger, so what is
    asserted is the record the application actually produced -- not a spy on
    the call site, and not structlog's global processor chain (which the
    repository's own unit test records as unreliable once another test has
    bound the logger).
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self) -> list[dict[str, Any]]:
        """The captured records as event dictionaries."""
        return [_as_event(record) for record in self.records]


def _as_event(record: logging.LogRecord) -> dict[str, Any]:
    """One captured record as a dictionary of its event fields."""
    message = record.msg
    if isinstance(message, dict):
        return dict(message)
    return {"event": str(message), "level": record.levelname}


@pytest.fixture(scope="module", autouse=True)
def _the_repositorys_logging_configuration() -> Iterator[None]:
    """Install the repository's real logging configuration for this module.

    The e2e ``client`` fixture drives the application over ``ASGITransport``,
    and its own comment says "ASGITransport doesn't trigger lifespan" -- so
    the application's lifespan never runs, and ``setup_logging()`` (which the
    lifespan calls) is never called. Without this fixture structlog stays at
    its AMBIENT default, whose predicate is structurally always True, and
    every assertion in this file would be vacuous: the script would be
    "proving" the pre-A-3 behaviour. See assertion (d).
    """
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    saved_levels = {name: logging.getLogger(name).level for name in ("uvicorn.access", "httpx", "httpcore")}

    setup_logging()
    try:
        yield
    finally:
        shutdown_logging()
        structlog.reset_defaults()
        root.setLevel(saved_level)
        root.handlers[:] = saved_handlers
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)


async def _drive(
    client: Any,
    *,
    level: int,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    user_agent: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Drive one request at ONE level and record what came back.

    The module's stdlib logger is raised to ``level`` for the duration of the
    request, so the predicate under test is the one that level implies. The
    level is restored afterwards, because the module logger's level is
    process-global state.
    """
    headers: dict[str, str] = {}
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    module_logger = logging.getLogger(MODULE_LOGGER)
    saved_level = module_logger.level
    capture = _RecordCapture()
    module_logger.addHandler(capture)
    module_logger.setLevel(level)
    try:
        predicate = ad.logger.is_enabled_for(logging.DEBUG)
        started = time.perf_counter()
        if body is None:
            response = await client.request(method, path, headers=headers)
        else:
            response = await client.request(method, path, json=body, headers=headers)
        client_observed_ms = (time.perf_counter() - started) * 1000.0
        events = capture.events()
    finally:
        module_logger.removeHandler(capture)
        module_logger.setLevel(saved_level)

    server_observed = response.headers.get("X-Response-Time")
    return {
        "level": logging.getLevelName(level),
        "predicate": predicate,
        "status_code": response.status_code,
        "request_id": response.headers.get("X-Request-ID"),
        "client_observed_ms": round(client_observed_ms, 3),
        "server_observed_ms": server_observed,
        "server_observed_note": (
            ""
            if server_observed is not None
            else (
                "absent: the RequestTimer is off unless REQUEST_TIMER_ENABLED was set when the "
                "application was CONSTRUCTED (the flag is read once, at import of the guard-ordering "
                "module), and the header is never written on the disabled path"
            )
        ),
        "events": events,
    }


async def _case(
    client: Any,
    *,
    name: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    user_agent: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Run one case at INFO and at DEBUG, and summarise what it showed."""
    info = await _drive(
        client,
        level=logging.INFO,
        method=method,
        path=path,
        body=body,
        user_agent=user_agent,
        token=token,
    )
    debug = await _drive(
        client,
        level=logging.DEBUG,
        method=method,
        path=path,
        body=body,
        user_agent=user_agent,
        token=token,
    )

    # The body-scan branch is observable only at DEBUG: the skip event is what
    # the guard emits when it does NOT enter the body branch.
    skipped_at_debug = any(event.get("event") == SKIP_EVENT for event in debug["events"])
    detected = _detections(debug["events"])

    return {
        "case": name,
        "method": method,
        "path": path,
        "has_body": body is not None,
        "user_agent": user_agent,
        "arms": {"info": info, "debug": debug},
        "body_scan_branch": "skipped" if skipped_at_debug else "entered",
        "selected_branch": detected[0]["location"] if detected else "no_attack",
        "detected": detected,
    }


def _detections(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The attacks detected in a batch of events, with their recorded reason."""
    return [
        {
            "type": event.get("type"),
            "pattern": event.get("pattern"),
            "location": event.get("location"),
        }
        for event in events
        if event.get("event") == DETECTED_EVENT
    ]


def _event_names(events: list[dict[str, Any]]) -> list[str]:
    """The names of the events in a batch, sorted and de-duplicated."""
    return sorted({str(event.get("event")) for event in events})


async def _probe(client: Any, token: str, username: str) -> list[dict[str, Any]]:
    """Every case this script drives, in order."""
    post_path = POST_PATH_TEMPLATE.format(username=username)
    return [
        await _case(
            client,
            name="get_bodyless",
            method="GET",
            path=BODYLESS_PATH,
            token=token,
        ),
        await _case(
            client,
            name="post_body_benign",
            method="POST",
            path=post_path,
            body=BENIGN_BODY,
            token=token,
        ),
        await _case(
            client,
            name="post_body_sqli",
            method="POST",
            path=post_path,
            body=SQLI_BODY,
            token=token,
        ),
        await _case(
            client,
            name="get_user_agent_benign_pixel",
            method="GET",
            path=BODYLESS_PATH,
            user_agent=PIXEL_USER_AGENT,
            token=token,
        ),
        await _case(
            client,
            name="get_user_agent_command",
            method="GET",
            path=BODYLESS_PATH,
            user_agent=COMMAND_USER_AGENT,
            token=token,
        ),
    ]


def _stable_facts(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The parts of a run that are a BEHAVIOUR rather than an observation.

    The status codes, the latencies and the request ids are observations: they
    depend on the host, the clock and the run. The method, the branch the gate
    selected, the predicate's value at each level, the detected attacks and
    the events that were emitted are the behaviour, and those are what the
    recorded artefact is compared on.
    """
    return [
        {
            "case": case["case"],
            "method": case["method"],
            "path": case["path"],
            "body_scan_branch": case["body_scan_branch"],
            "selected_branch": case["selected_branch"],
            "detected": case["detected"],
            "predicate_at_info": case["arms"]["info"]["predicate"],
            "predicate_at_debug": case["arms"]["debug"]["predicate"],
            "status_at_info": case["arms"]["info"]["status_code"],
            "status_at_debug": case["arms"]["debug"]["status_code"],
            "events_at_info": _event_names(case["arms"]["info"]["events"]),
            "events_at_debug": _event_names(case["arms"]["debug"]["events"]),
        }
        for case in cases
    ]


def _artefact(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The recorded artefact for this run."""
    return {
        "schema": "s8_attack_scan_gate_e2e/1",
        "bead": "FastAPI_saas-xfpt",
        "item": "S-8",
        "title": (
            "The attack-scan method gate is driven over the real ASGI stack and the real logging "
            "stack: the predicate is False at INFO and True at DEBUG, a bodyless GET does not "
            "enter the body-scan gate while a POST with a body does, and the tripping user-agent "
            "trips for the recorded reason."
        ),
        "quantity": (
            "Behaviour, not latency. S-8's own arithmetic is 0 us, 0.00 % of the 4.09 ms "
            "uncontended floor and 0.00 % of the 740 ms p95, so this moves capacity, not p95."
        ),
        "method": {
            "driver": "tests/e2e/feature_validation/test_s8_attack_scan_gate_e2e.py",
            "in_repo": True,
            "stack": "the real application over ASGITransport, with the real middleware stack",
            "logging_configuration": "src/app/core/logger.py::setup_logging, installed by the script",
            "hard_gate": "the bound logger is asserted to be a LevelAwareBoundLogger (A-3 landed)",
            "levels": "each case is driven twice: the module logger at INFO, then at DEBUG",
            "records": "the REAL log records, captured on the module's own stdlib logger",
            "server_clock": {
                "header": "X-Response-Time",
                "enabled_in_this_run": os.environ.get("REQUEST_TIMER_ENABLED", "").strip().lower()
                in ("1", "true", "yes", "on"),
                "note": (
                    "the flag is read once, when the guard-ordering module is imported, so a run "
                    "that sets it after the application was constructed still sees no header; the "
                    "absence is recorded per request with that reason"
                ),
            },
        },
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "host_relevant": False,
            "host_relevant_why": (
                "the quantity is a behaviour -- which branch the gate selected and whether an "
                "event was emitted -- and not a duration"
            ),
        },
        "cases": cases,
        "stable_facts": _stable_facts(cases),
        "verdict": {
            "statement": (
                "The guard is evaluated on the real stack and closes at INFO: the bodyless GET "
                "emits the skip event at DEBUG and none at INFO, the POST's body is scanned and a "
                "SQLi body is detected in request_body, and the user-agent that carries the "
                "command in command position is detected in header:user-agent while the one that "
                "carries the same substring mid-word is not detected at all."
            ),
            "body_scan_branch": {case["case"]: case["body_scan_branch"] for case in cases},
            "selected_branch": {case["case"]: case["selected_branch"] for case in cases},
            "detected": {case["case"]: case["detected"] for case in cases},
        },
        "limits": [
            "The requests are driven in-process over ASGITransport, so the application's lifespan "
            "never runs. The script therefore installs the logging configuration itself, and "
            "asserts that the bound logger is a LevelAwareBoundLogger -- without that the "
            "predicate would be structurally always True and every assertion here would be "
            "vacuous.",
            "The server-side clock is absent unless REQUEST_TIMER_ENABLED was set when the "
            "application was constructed; the reason for its absence is recorded per request "
            "rather than papered over. No latency threshold is applied to either clock.",
            "The attack-detection mode is read from the running configuration, so a deployment "
            "configured for 'log' or 'ban' is reported on its own terms rather than being forced "
            "into 'block'.",
        ],
    }


@pytest.fixture
async def probe(client: Any, authenticated_user: dict[str, Any], log_step: Any) -> list[dict[str, Any]]:
    """Drive every case once, and share the result with the assertions.

    The run happens ONCE per session: it issues real requests and creates a
    post, and every assertion below reads the same recorded run.
    """
    global _PROBE
    if _PROBE is None:
        cases = await _probe(client, authenticated_user["access_token"], authenticated_user["username"])
        for case in cases:
            log_step(
                f"s8 case {case['case']}",
                method=case["method"],
                path=case["path"],
                body_scan_branch=case["body_scan_branch"],
                selected_branch=case["selected_branch"],
                predicate_at_info=case["arms"]["info"]["predicate"],
                predicate_at_debug=case["arms"]["debug"]["predicate"],
                status_at_info=case["arms"]["info"]["status_code"],
                status_at_debug=case["arms"]["debug"]["status_code"],
            )
        if os.environ.get(WRITE_ARTEFACT_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
            ARTEFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
            ARTEFACT_PATH.write_text(json.dumps(_artefact(cases), indent=2, default=str), encoding="utf-8")
        _PROBE = cases
    return _PROBE


def _case_named(cases: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for case in cases:
        if case["case"] == name:
            return case
    raise AssertionError(f"no case named {name!r} in {[case['case'] for case in cases]}")


def _attack_detection_mode() -> str:
    """The attack-detection mode this run is actually configured for."""
    from src.app.core.config import settings

    return settings.attack_detection.ATTACK_DETECTION_MODE


def _rejection_status(mode: str) -> int:
    """The status a detected attack is rejected with in ``mode``."""
    return {"block": 400, "ban": 403}.get(mode, 200)


class TestThePredicateIsTheRealOne:
    """Assertion (a) and the hard gate (d)."""

    async def test_the_bound_logger_is_the_delegating_one(self) -> None:
        """(d) A-3 landed: the predicate delegates instead of freezing a minimum.

        Against structlog's ambient default the bound logger is
        ``BoundLoggerFilteringAtNotset``, whose predicate is structurally
        always True. Asserting the concrete class is what makes the
        assertions below mean anything.
        """
        bound = ad.logger.bind()
        assert type(bound) is LevelAwareBoundLogger, (
            "the module's logger is bound to "
            f"{type(bound).__name__}, not LevelAwareBoundLogger: the repository's logging "
            "configuration was not installed, so the predicate under test would be the ambient "
            "always-True one and every assertion in this file would be vacuous"
        )

    async def test_the_predicate_is_false_at_info_and_true_at_debug(self, probe: list[dict[str, Any]]) -> None:
        """(a) The predicate's VALUE at each level, asserted as an equality.

        Asserted with ``is False`` / ``is True`` rather than by truthiness: a
        truthiness assertion would also be satisfied by a predicate that is
        None, and "is True" alone would be satisfied by the always-True
        default this exercise exists to rule out.
        """
        for case in probe:
            assert case["arms"]["info"]["predicate"] is False, (
                f"case {case['case']!r}: the predicate was not False at INFO "
                f"(got {case['arms']['info']['predicate']!r})"
            )
            assert case["arms"]["debug"]["predicate"] is True, (
                f"case {case['case']!r}: the predicate was not True at DEBUG "
                f"(got {case['arms']['debug']['predicate']!r})"
            )

    async def test_the_skip_event_is_emitted_only_above_debug(self, probe: list[dict[str, Any]]) -> None:
        """The observable consequence of (a): the event follows the predicate.

        At INFO the guard closes, so the skip event is never emitted. At
        DEBUG the guard opens and the event is emitted. A guard that emitted
        the event at INFO would be logging a per-request line on every
        bodyless request in production.
        """
        for case in probe:
            info_events = _event_names(case["arms"]["info"]["events"])
            debug_events = _event_names(case["arms"]["debug"]["events"])
            if case["method"] in ("GET", "HEAD", "OPTIONS", "DELETE", "TRACE", "CONNECT"):
                assert SKIP_EVENT not in info_events, (
                    f"case {case['case']!r}: the skip event was emitted at INFO: {info_events}"
                )
                assert SKIP_EVENT in debug_events, (
                    f"case {case['case']!r}: the skip event was not emitted at DEBUG: {debug_events}"
                )


class TestTheBodyScanGate:
    """Assertion (b): the GET/POST pair."""

    async def test_a_bodyless_get_does_not_enter_the_body_scan_gate(self, probe: list[dict[str, Any]]) -> None:
        """The GET leg: the skip event is emitted, so the body branch was not entered."""
        case = _case_named(probe, "get_bodyless")
        assert case["body_scan_branch"] == "skipped"
        assert case["selected_branch"] == "no_attack"

    async def test_a_bodyless_get_is_served(self, probe: list[dict[str, Any]]) -> None:
        """The GET leg: the request is served, so the skip is not a rejection."""
        case = _case_named(probe, "get_bodyless")
        assert case["arms"]["debug"]["status_code"] == 200
        assert case["arms"]["info"]["status_code"] == 200

    async def test_a_post_with_a_body_does_enter_the_body_scan_gate(self, probe: list[dict[str, Any]]) -> None:
        """The POST leg: the skip event is NOT emitted, so the body branch WAS entered."""
        case = _case_named(probe, "post_body_benign")
        assert case["body_scan_branch"] == "entered"
        debug_events = _event_names(case["arms"]["debug"]["events"])
        assert SKIP_EVENT not in debug_events, (
            f"the skip event was emitted for a POST, so the body-scan gate was not entered: {debug_events}"
        )

    async def test_a_benign_body_is_scanned_and_passes(self, probe: list[dict[str, Any]]) -> None:
        """The POST leg: a clean body is scanned and the request is served."""
        case = _case_named(probe, "post_body_benign")
        assert case["arms"]["debug"]["status_code"] == 201
        assert case["selected_branch"] == "no_attack"

    async def test_a_sqli_body_is_detected_in_the_body(self, probe: list[dict[str, Any]]) -> None:
        """The POST leg, from the other side: the body really is scanned.

        This is the positive proof that the body branch was entered. A body
        carrying a SQLi payload is detected with ``location == "request_body"``
        and the request is rejected; had the body NOT been scanned, the
        request would have been created instead.
        """
        case = _case_named(probe, "post_body_sqli")
        assert case["body_scan_branch"] == "entered"
        assert case["selected_branch"] == "request_body"
        assert case["detected"], "the SQLi body was not detected at all"
        detection = case["detected"][0]
        assert detection["location"] == "request_body"
        assert detection["type"] == "sql_injection"
        assert detection["pattern"] == "sql_union_select"

        mode = _attack_detection_mode()
        if mode == "log":
            assert case["arms"]["debug"]["status_code"] != 400
        else:
            assert case["arms"]["debug"]["status_code"] == _rejection_status(mode), (
                f"the SQLi body was not rejected in mode {mode!r}: "
                f"got {case['arms']['debug']['status_code']}"
            )


class TestTheTrippingUserAgent:
    """Assertion (c): the tripping user-agent, and the recorded reason."""

    async def test_the_user_agent_with_the_command_in_command_position_trips(self, probe: list[dict[str, Any]]) -> None:
        """The tripping leg: the command is detected, in the header."""
        case = _case_named(probe, "get_user_agent_command")
        assert case["selected_branch"] == "header:user-agent", (
            f"the tripping user-agent was not detected in the header: {case['detected']}"
        )
        assert case["detected"], "the tripping user-agent produced no detection"
        detection = case["detected"][0]
        assert detection["type"] == "command_injection"
        assert detection["pattern"] == "command_injection"
        assert detection["location"] == "header:user-agent"

    async def test_the_tripping_user_agent_is_rejected(self, probe: list[dict[str, Any]]) -> None:
        """The tripping leg: the request is rejected, in the mode's own terms."""
        case = _case_named(probe, "get_user_agent_command")
        mode = _attack_detection_mode()
        if mode == "log":
            assert case["arms"]["debug"]["status_code"] != 400
        else:
            assert case["arms"]["debug"]["status_code"] == _rejection_status(mode)

    async def test_the_user_agent_with_the_same_substring_mid_word_does_not_trip(
        self, probe: list[dict[str, Any]]
    ) -> None:
        """The benign leg: the same substring, a different position, no detection."""
        case = _case_named(probe, "get_user_agent_benign_pixel")
        assert case["detected"] == [], (
            f"the benign user-agent was detected, and for the wrong reason: {case['detected']}"
        )
        assert case["selected_branch"] == "no_attack"
        assert case["arms"]["debug"]["status_code"] == 200

    async def test_the_recorded_reason_is_the_position_and_not_the_absence(self) -> None:
        """The attribution itself, asserted on the strings.

        "Pixel" contains no "id"; "Android" does. The benign verdict is
        therefore NOT explained by the absence of the substring -- the "id"
        inside "Android" is present and the request is still clean, because
        the substring is mid-word and never sits at ";" + command position.
        A test that asserted the absence would pass for the wrong reason and
        would be broken incorrectly the moment the attribution was fixed.
        """
        assert "id" not in "pixel"
        assert "id" in "android"

        # Both user-agents carry the substring; only the one with the command
        # in command position trips.
        assert "id" in PIXEL_USER_AGENT.lower()
        assert "id" in COMMAND_USER_AGENT.lower()

        # And the position is the discriminator, by the middleware's own
        # compiled pattern.
        assert "; id" not in PIXEL_USER_AGENT
        assert "; id" in COMMAND_USER_AGENT
        # The combined pattern is BYTES, so it is DECODED before comparison:
        # str() of a bytes pattern is its repr, in which every backslash is
        # doubled, and the single-backslash source would never be a substring
        # of it -- a check that would fail for a reason that has nothing to do
        # with the attribution.
        assert COMMAND_PATTERN in ad.COMBINED_ATTACK_RE.pattern.decode()

    async def test_the_two_user_agents_differ_only_in_the_position_of_the_command(self) -> None:
        """The differential, stated as a fact about the two strings.

        The benign user-agent and the tripping one are not two unrelated
        strings: both carry "id", and the tripping one carries it where the
        pattern matches. The reason the benign one is clean is that its "id"
        is mid-word.
        """
        assert "Android" in PIXEL_USER_AGENT
        assert "Pixel" in PIXEL_USER_AGENT
        assert ad.COMBINED_ATTACK_RE.search(PIXEL_USER_AGENT.encode()) is None, (
            "the benign user-agent matched the combined regex, so it is not benign any more"
        )
        assert ad.COMBINED_ATTACK_RE.search(COMMAND_USER_AGENT.encode()) is not None, (
            "the tripping user-agent did not match the combined regex, so the tripping leg is broken"
        )


class TestTheRunIsRecorded:
    """The artefact is a record of this run, not a story about one."""

    async def test_every_case_records_what_the_bead_asks_for(self, probe: list[dict[str, Any]]) -> None:
        """Per request: the method, the status, the branch, the predicate, the records."""
        for case in probe:
            for level in ("info", "debug"):
                arm = case["arms"][level]
                assert isinstance(arm["client_observed_ms"], float)
                assert arm["server_observed_ms"] is None or isinstance(arm["server_observed_ms"], str)
                if arm["server_observed_ms"] is None:
                    assert arm["server_observed_note"], (
                        f"case {case['case']!r}: the server clock is absent and the reason is not recorded"
                    )
            assert case["body_scan_branch"] in ("entered", "skipped")
            assert case["selected_branch"]
            assert case["arms"]["info"]["status_code"] > 0
            assert case["arms"]["debug"]["status_code"] > 0

    async def test_the_request_id_is_carried_when_the_request_reaches_the_handler(
        self, probe: list[dict[str, Any]]
    ) -> None:
        """The request id is echoed on a served response.

        It is absent on a request the ATTACK DETECTOR rejected, because the
        request-id middleware is INSIDE the attack detector in the guard
        order, so it never ran. That is recorded rather than papered over:
        the field is a str or None, never a blank string.
        """
        served = _case_named(probe, "get_bodyless")
        assert served["arms"]["debug"]["request_id"]
        for case in probe:
            for level in ("info", "debug"):
                request_id = case["arms"][level]["request_id"]
                assert request_id is None or isinstance(request_id, str)

    async def test_the_recorded_artefact_is_this_runs_facts(self, probe: list[dict[str, Any]]) -> None:
        """The committed artefact matches what this run measured.

        The comparison is on the BEHAVIOUR (the branch selected, the detected
        attacks, the predicate at each level, the events emitted). The
        observations -- the status codes, the latencies, the request ids --
        are recorded in the artefact but are not what it is compared on, since
        they belong to the host and the clock rather than to the code.
        """
        assert ARTEFACT_PATH.exists(), (
            f"the recorded artefact is missing: {ARTEFACT_PATH}. Run the script with "
            f"{WRITE_ARTEFACT_ENV}=1 to produce it."
        )
        recorded = json.loads(ARTEFACT_PATH.read_text(encoding="utf-8"))
        assert recorded["schema"] == "s8_attack_scan_gate_e2e/1"
        assert recorded["bead"] == "FastAPI_saas-xfpt"

        recorded_facts = {fact["case"]: fact for fact in recorded["stable_facts"]}
        live_facts = {fact["case"]: fact for fact in _stable_facts(probe)}
        assert set(recorded_facts) == set(live_facts), (
            f"the recorded artefact drives different cases: {sorted(recorded_facts)} vs {sorted(live_facts)}"
        )
        for name, live in live_facts.items():
            recorded_case = recorded_facts[name]
            for key in (
                "method",
                "path",
                "body_scan_branch",
                "selected_branch",
                "detected",
                "predicate_at_info",
                "predicate_at_debug",
                "events_at_info",
                "events_at_debug",
            ):
                assert recorded_case[key] == live[key], (
                    f"case {name!r}: the recorded artefact says {key}={recorded_case[key]!r} "
                    f"but this run measured {live[key]!r}"
                )
