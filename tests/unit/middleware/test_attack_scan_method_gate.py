"""nrgi.3: the attack-detection body scan is method-gated, and the skip is logged.

Measured facts this pins (docs/adr/0008, bead FastAPI_saas-nrgi.3):

* ``_scan_request`` drains and scans a body only for POST/PUT/PATCH. For
  every other method the body is never read at all — so an attacker cannot
  make the middleware buffer a GET body by omitting Content-Length.
* The GET/HEAD/... skip is logged at DEBUG, guarded by ``is_enabled_for``
  because a bare structlog ``debug()`` costs ~44.6us — 6.7x the whole
  bodyless-GET scan it announces — while the guard costs ~1.9us.
* The skip removes no coverage: path, query string and headers are scanned
  for EVERY method, above the gate.

The 10 MB / 413 body limit and the deep-payload detection counter-test live
in tests/e2e/core/security/test_attack_scan_bounds_e2e.py.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, MutableMapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog

from src.app.middleware import attack_detection as ad
from src.app.middleware.attack_detection import (
    _BODY_METHODS,
    AttackDetectionMiddleware,
)

#: A payload that trips several regex groups at once.
SQLI = b"UNION SELECT password FROM users"

#: The methods whose body the scan must never read.
BODYLESS = ["GET", "HEAD", "OPTIONS", "DELETE", "TRACE", "CONNECT"]


MODULE_LOGGER = "src.app.middleware.attack_detection"


@pytest.fixture
def middleware() -> AttackDetectionMiddleware:
    return AttackDetectionMiddleware(app=MagicMock())


@pytest.fixture(scope="module", autouse=True)
def _the_repositorys_logging_configuration() -> Iterator[None]:
    """Install this repo's REAL logging configuration for the whole module.

    A-3 makes ``is_enabled_for`` consult the effective stdlib level, which is
    only true once ``setup_logging`` has run -- nothing in a test session runs
    it for us. Without this fixture these tests would keep exercising
    structlog's AMBIENT default (``BoundLoggerFilteringAtNotset``, whose
    predicate is structurally always True) and would pass for a reason that has
    nothing to do with the code under test. So the configuration the middleware
    sees in production is the configuration it is asserted against here.

    ``setup_logging`` clears the root handlers, sets the root level, opens a
    rotating file handler, starts a QueueListener thread and silences
    ``uvicorn.access``/``httpx``/``httpcore``; all of it is restored here so the
    rest of the session is unaffected.
    """
    from src.app.core.logger import setup_logging, shutdown_logging

    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = root.handlers[:]
    silenced = {
        name: logging.getLogger(name).level
        for name in ("uvicorn.access", "httpx", "httpcore")
    }
    setup_logging()
    try:
        yield
    finally:
        shutdown_logging()
        structlog.reset_defaults()
        root.setLevel(saved_level)
        root.handlers[:] = saved_handlers
        for name, level in silenced.items():
            logging.getLogger(name).setLevel(level)


@pytest.fixture
def module_logger_at_debug() -> Iterator[None]:
    """Raise THIS module's stdlib logger to DEBUG for the duration.

    The pre-flight for A-3, and deliberately NOT a weakening. The middleware
    guards its skip event with ``is_enabled_for(DEBUG)``, and A-3 makes that
    predicate delegate to the stdlib level -- so at the default level the guard
    now (correctly) short-circuits and no event is emitted at all. A test whose
    subject is "the skip IS logged, with these fields" has to turn DEBUG on for
    the logger it is about; asserting ``len(calls) == 0`` instead would be
    asserting the absence of the thing the test exists to pin.
    """
    logger = logging.getLogger(MODULE_LOGGER)
    saved = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.setLevel(saved)



def _scope(method: str, body: bytes = b"", *, headers: list | None = None, query: bytes = b""):
    """Native-ASGI (scope, receive) pair; ``receive`` yields ``body`` once."""
    hdrs = headers if headers is not None else (
        [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        if body
        else []
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/api/v1/files",
        "query_string": query,
        "headers": hdrs,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    chunks = [body] if body else []

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    return scope, receive


async def _scan(mw: AttackDetectionMiddleware, method: str, body: bytes = b"", **kw):
    scope, receive = _scope(method, body, **kw)
    return await AttackDetectionMiddleware._scan_request(
        mw, scope["path"], scope["query_string"], scope["headers"], scope["method"], receive
    )


def _skips(events: list[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [r for r in events if "skipped_body_scan" in r.get("event", "")]


# --- the gate itself --------------------------------------------------------


def test_body_methods_is_exactly_post_put_patch() -> None:
    """The gate is a named fact, not an inline literal buried mid-function."""
    assert _BODY_METHODS == {"POST", "PUT", "PATCH"}
    for bodyless in BODYLESS:
        assert bodyless not in _BODY_METHODS


@pytest.mark.parametrize("method", BODYLESS)
async def test_bodyless_methods_never_drain_the_body(method: str, middleware: AttackDetectionMiddleware) -> None:
    """The body must not even be READ — buffering it is the cost being avoided.

    Asserted by counting ``_drain_body`` calls, not by the absence of a
    finding: a method that drained but scanned nothing would look identical
    from the verdict alone, yet would still pay the buffering cost.
    """
    calls = 0
    original = middleware._drain_body

    async def counting(receive):
        nonlocal calls
        calls += 1
        return await original(receive)

    with patch.object(middleware, "_drain_body", counting):
        attacks, _ = await _scan(middleware, method, SQLI)

    assert calls == 0, f"{method} drained the body {calls} time(s) — GET-body scanning is back"
    assert attacks == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
async def test_body_methods_do_drain_and_scan(method: str, middleware: AttackDetectionMiddleware) -> None:
    """Counterpart: the gate must not have been widened into a no-op."""
    attacks, _ = await _scan(middleware, method, SQLI)
    assert [a["location"] for a in attacks] == ["request_body"]
    assert any(a["pattern_name"] == "sql_union_select" for a in attacks)


# --- the skip is logged at debug -------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_bodyless_skip_is_logged_at_debug(
    method: str, middleware: AttackDetectionMiddleware, module_logger_at_debug: None
) -> None:
    """The skip emits exactly one DEBUG event carrying queryable fields.

    Asserted by spying on the module logger's own ``debug`` call, not via
    ``structlog.testing.capture_logs()``. That helper swaps the GLOBAL
    processor chain, and whether the module's already-bound logger picks the
    swap up depends on which tests ran first in the session — so it returned an
    empty list in a full run while passing in isolation, twice, for two
    different files. The same property is observable at the call site, which is
    also where ``test_skip_is_silent_below_debug`` observes it.
    """
    calls: list[tuple[tuple, dict]] = []

    def spy(event, *args, **kwargs):
        calls.append(((event, *args), kwargs))

    # S-8 de-mock: no is_enabled_for patch — the real predicate runs, on a
    # logger this test has configured to DEBUG (see module_logger_at_debug).
    # Which DEBUG level gated the call is now asserted by
    # test_debug_guard_is_level_aware.
    with patch.object(ad.logger, "debug", side_effect=spy):
        await _scan(middleware, method)

    assert len(calls) == 1, f"expected one skip event for {method}, got {calls}"
    (event_args, fields) = calls[0]
    event = event_args[0]
    assert "skipped_body_scan" in event
    assert fields["method"] == method
    assert fields["path"] == "/api/v1/files"
    # P1-12 contract: fields, not a pre-formatted sentence.
    assert "method=" not in str(event)


async def test_skip_is_silent_below_debug(middleware: AttackDetectionMiddleware) -> None:
    """With DEBUG disabled the log call must not even be ATTEMPTED.

    This is what the ``is_enabled_for`` guard buys. An unguarded
    ``logger.debug()`` through this stack costs ~44.6us — 6.7x the 6.7us
    bodyless-GET scan it would announce — so at the default WARNING level
    the call has to short-circuit before structlog does any work.

    ``structlog.testing.capture_logs`` cannot demonstrate this: it installs
    a capturing processor that accepts every level, so it reports the
    event even with DEBUG genuinely disabled. The contract under test is
    the real logger's level gate, so the real module logger is spied on.
    """
    called: list = []

    def spy(*args, **kwargs):  # pragma: no cover - must never run
        called.append((args, kwargs))

    with patch.object(ad.logger, "debug", side_effect=spy):
        await _scan(middleware, "GET")

    assert called == [], "logger.debug was invoked despite DEBUG being disabled"


def test_debug_guard_is_level_aware() -> None:
    """The DEBUG guard must actually consult the level — not return True always.

    S-8 oracle, de-mocked: the real module logger, no patches. On HEAD this
    FAILS by design (the predicate is ``level >= min_level`` with the default
    filtering bound logger, structurally always True). A-3 lands the
    level-aware ``wrapper_class`` and this flips green.

    This is the INFO half of S-8's acceptance criterion ("the real predicate
    is asserted at DEBUG and at INFO"). The DEBUG half is
    ``test_debug_guard_is_level_aware_at_debug``: both directions are needed,
    because a predicate frozen at a minimum is False at DEBUG and False at
    INFO alike, and one that is always True is True at both.
    """
    import logging as _logging

    assert ad.logger.is_enabled_for(_logging.DEBUG) is False


def test_debug_guard_is_level_aware_at_debug(module_logger_at_debug: None) -> None:
    """With THIS module's logger at DEBUG, the same predicate must be True.

    The other half of S-8's acceptance criterion, and the half that
    distinguishes a DELEGATING predicate from a frozen-minimum one:
    ``structlog.make_filtering_bound_logger(logging.INFO)`` returns a
    ``BoundLoggerFilteringAtInfo`` whose ``is_enabled_for(DEBUG)`` is False —
    the same value the INFO half above asserts, for a completely different
    reason. A predicate that is False at INFO and False at DEBUG has closed
    the gate in both directions and would silently drop the skip event for
    the operators who asked for it.

    The logger raised here is the same stdlib logger the bound logger
    delegates to (``LevelAwareBoundLogger.is_enabled_for`` ->
    ``self._logger.isEnabledFor``), and the fixture restores its level
    afterwards.
    """
    import logging as _logging

    assert ad.logger.is_enabled_for(_logging.DEBUG) is True


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
async def test_scanned_methods_do_not_log_a_skip(method: str, middleware: AttackDetectionMiddleware) -> None:
    """A method that IS scanned must never emit the skip event."""
    with structlog.testing.capture_logs() as captured:
        await _scan(middleware, method, b'{"a": 1}')
    assert not _skips(captured)


# --- the skip removes no attack coverage -----------------------------------


async def test_get_query_string_is_still_scanned(middleware: AttackDetectionMiddleware) -> None:
    """A GET carries its attack surface in the query string — that path stays."""
    attacks, _ = await _scan(middleware, "GET", query=b"q=' OR 1=1")
    assert any(a["pattern_name"] == "sql_or_1_equals_1" for a in attacks)
    assert all(a["location"] == "query_string" for a in attacks)


async def test_get_path_is_still_scanned(middleware: AttackDetectionMiddleware) -> None:
    scope, receive = _scope("GET", headers=[])
    scope["path"] = "/api/v1/files/../../etc/passwd"
    attacks, _ = await AttackDetectionMiddleware._scan_request(
        middleware, scope["path"], scope["query_string"], scope["headers"], scope["method"], receive
    )
    assert any(a["pattern_name"] == "path_dotdot_slash" for a in attacks)
    assert all(a["location"] == "url_path" for a in attacks)


async def test_header_is_still_scanned_on_a_get(middleware: AttackDetectionMiddleware) -> None:
    """Headers are scanned above the gate, for every method."""
    attacks, _ = await _scan(
        middleware,
        "GET",
        headers=[(b"referer", b"http://169.254.169.254/latest/meta-data/")],
    )
    assert any(a["pattern_name"] == "ssrf_cloud_metadata" for a in attacks)
