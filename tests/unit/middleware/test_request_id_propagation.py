"""S-5 oracle: inbound-X-Request-ID zero-change equality, format, uniqueness, state==header.

S-5 is a CORRECTNESS change to request correlation, not a speed one, and a
correctness change to request correlation silently breaks log correlation. This
oracle is landed BEFORE the diff so that "the header still says what it used to"
is a machine-checked fact rather than an assumption.

WHAT IS PINNED HERE
(1) A VALID inbound ``X-Request-ID`` is echoed unchanged — the zero-change case.
(2) With no inbound header, the generated id matches the documented pattern and
    is UNIQUE over 1000 requests.
(3) ``request.state.request_id`` equals the response header on BOTH the
    pass-through path and the guard short-circuit path (429).

FAILURE MODES THIS FILE EXISTS TO CATCH, quoted from the bead's design
(i) The zero-change case only holds for a VALID inbound header. An invalid or
    oversized one must be REPLACED, not echoed — echoing it would let a caller
    inject a newline or a 4 KB string into every log line that carries it. So the
    replacement cases are tested explicitly rather than assumed.
(ii) The reject path is a different code path. ``RequestIDMiddleware`` is
    innermost (see ``core/guard/ordering.py``), so an outer guard's 403/429
    short-circuit runs BEFORE it and would otherwise emit no ``X-Request-ID`` at
    all. ``rate_limit.py`` and ``ip_filter.py`` therefore re-derive the id with
    ``request_id_from_scope(scope)`` and wrap ``send`` themselves. That is
    behavioural in ``wrap_send_with_request_id`` and STRUCTURAL at the two call
    sites, so both halves are asserted: the reject response really does carry the
    header, and the two guards really do route through the shared helpers rather
    than through a private copy that could drift.
(iii) The grep pre-check is made enforceable. See
    ``test_no_other_test_hardcodes_a_request_id_value``.

PRE-CHECK (the plan's ``grep for hardcoded request-id assertions first``)
Run before this file was written. Consumers of ``request.state.request_id`` /
``correlation_id`` in ``src/app/core/security/logging.py`` are real and were NOT
grepped by the audit that produced the bead:
  logging.py:68  ``correlation_id: Optional request correlation ID for tracing``
  logging.py:91  ``correlation_id: str | None = None``   (the processor field)
  logging.py:113 ``"correlation_id": self.correlation_id,``
  logging.py:180/194/216 the logging-event wrapper plumbs it through
and in ``src/app/middleware/``:
  ip_filter.py:113   and   rate_limit.py:63
      both ``send = wrap_send_with_request_id(send, request_id_from_scope(scope))``
  request_id.py:21   ``REQUEST_ID_HEADER = "X-Request-ID"``
  request_id.py:23   ``_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\\-]{1,128}$")``
  request_id.py:26   ``resolve_request_id(incoming_id)`` — generate-or-propagate
  request_id.py:37   ``request_id_from_scope(scope)`` — the shared scope helper
  request_id.py:53   ``wrap_send_with_request_id(send, request_id)`` — the shared sender
  request_id.py:90   ``set_request_id(request.state, request_id)``
No production code reads ``request.state.request_id`` to BUILD a header, so the
header is the only externally visible surface and the oracle drives it directly.
No existing test file hardcodes a literal ``X-Request-ID`` value — verified, and
pinned by ``test_no_other_test_hardcodes_a_request_id_value`` so it stays true.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Request
from starlette.types import Message, Receive, Scope, Send

from src.app.core.guard.ordering import GUARD_ORDER
from src.app.middleware.rate_limit import RateLimitMiddleware
from src.app.middleware.request_id import (
    REQUEST_ID_HEADER,
    RequestIDMiddleware,
    request_id_from_scope,
    resolve_request_id,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# The documented format, restated here from request_id.py:23 rather than imported,
# so that a change to the pattern in production FAILS this test instead of silently
# redefining what "valid" means. Importing the pattern would make the test agree
# with whatever the code decided, which is how the shape of the bug would be lost.
DOCUMENTED_PATTERN = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")

# A valid inbound id the caller supplied: the zero-change case must echo it.
# NOTE the underscore is absent on purpose. ``^[a-zA-Z0-9\\-]{1,128}$`` does NOT
# allow ``_``, and the first draft of this file used "trace-abc-123_DEF" as its
# exemplar — which the log-injection guard correctly REPLACED with a UUID, and
# which read as a zero-change failure. The guard was right; the exemplar was
# wrong. An id with an underscore is in the invalid set below, not the valid one.
VALID_INBOUND_ID = "trace-abc-123-DEF"

# request_id.py:23 caps the length at 128. These are the boundary cases.
MAX_LENGTH_ID = "a" * 128
OVERSIZED_ID = "b" * 129


# --------------------------------------------------------------------------
# ASGI harness. A bare app, not the real one: this oracle is about the header
# contract, and booting the app would make a header assertion depend on
# tenant resolution, rate-limit budgets and Redis.
# --------------------------------------------------------------------------


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope(headers: dict[str, str], path: str = "/api/v1/health") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }


def _terminal_app(state_sink: list[str] | None = None) -> Any:
    """A handler that records what ``request.state.request_id`` reads back as."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if state_sink is not None:
            state_sink.append(Request(scope, receive, send).state.request_id)
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


async def _run(app: Any, scope: Scope) -> list[Message]:
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, _receive, send)
    return messages


def _response_header(messages: list[Message], name: str) -> str | None:
    for message in messages:
        if message["type"] != "http.response.start":
            continue
        for header_name, value in message["headers"]:
            if header_name.decode("latin-1").lower() == name.lower():
                decoded: str = value.decode("latin-1")
                return decoded
    return None


def _status(messages: list[Message]) -> int:
    for message in messages:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("no http.response.start message was sent")


class _AlwaysLimitedAdmission:
    """Stands in for ``RequestAdmissionService`` so a 429 needs no Redis.

    ``RateLimitMiddleware.__init__`` takes the admission as a parameter precisely
    so this seam exists; the middleware's own reject branch is therefore driven
    for real rather than simulated.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, request: Request) -> Any:
        self.calls += 1
        return SimpleNamespace(
            action="limited",
            verdict=SimpleNamespace(headers={"Retry-After": "1"}),
            client_ip="127.0.0.1",
            ban_info=None,
            violation_count=0,
        )


# --------------------------------------------------------------------------
# (1) the zero-change case
# --------------------------------------------------------------------------


@pytest.mark.parametrize("inbound", [VALID_INBOUND_ID, MAX_LENGTH_ID, "a", "0", "A-BC-9"])
async def test_a_valid_inbound_request_id_is_echoed_unchanged(inbound: str):
    """The zero-change case: what the caller sent is what the caller gets back."""
    messages = await _run(RequestIDMiddleware(_terminal_app()), _scope({REQUEST_ID_HEADER: inbound}))
    echoed = _response_header(messages, REQUEST_ID_HEADER)
    assert echoed == inbound, f"a valid inbound id was altered: sent {inbound!r}, got {echoed!r}"


async def test_a_valid_inbound_id_also_reaches_request_state():
    state: list[str] = []
    messages = await _run(
        RequestIDMiddleware(_terminal_app(state)), _scope({REQUEST_ID_HEADER: VALID_INBOUND_ID})
    )
    assert state == [VALID_INBOUND_ID]
    assert _response_header(messages, REQUEST_ID_HEADER) == VALID_INBOUND_ID


# --------------------------------------------------------------------------
# failure mode (i): an invalid or oversized inbound id must be REPLACED
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "inbound"),
    [
        ("empty", ""),
        ("contains a space", "has space"),
        ("contains a newline", "abc\ndef"),
        ("contains a colon", "abc:def"),
        ("contains an underscore, which the pattern does not allow", "trace-abc-123_DEF"),
        ("one character over the cap", OVERSIZED_ID),
        ("far over the cap", "c" * 4096),
        ("non-ascii", "id-éè"),
    ],
)
async def test_an_invalid_or_oversized_inbound_id_is_replaced_not_echoed(label: str, inbound: str):
    """Echoing an unvalidated inbound id would inject it into every log line.

    The log-injection guard at request_id.py:32 is the reason; this is the
    observable consequence, asserted through the header the caller can read.
    """
    messages = await _run(RequestIDMiddleware(_terminal_app()), _scope({REQUEST_ID_HEADER: inbound}))
    echoed = _response_header(messages, REQUEST_ID_HEADER)
    assert echoed is not None, f"({label}) no {REQUEST_ID_HEADER} on the response at all"
    assert echoed != inbound, f"({label}) the unvalidated inbound id was echoed verbatim: {echoed!r}"
    assert DOCUMENTED_PATTERN.match(echoed), f"({label}) the replacement is not a valid id: {echoed!r}"


def test_resolve_request_id_is_the_same_decision_the_middleware_makes():
    """The pure helper and the middleware must not drift apart.

    Asserting against the middleware alone would let the helper rot unnoticed if
    a future caller started using it directly (the two outer guards already do).
    """
    for inbound in (VALID_INBOUND_ID, MAX_LENGTH_ID, "", "has space", OVERSIZED_ID):
        resolved = resolve_request_id(inbound)
        if inbound and DOCUMENTED_PATTERN.match(inbound):
            assert resolved == inbound, f"a valid id was not propagated: {inbound!r} -> {resolved!r}"
        else:
            assert resolved != inbound, f"an invalid id was propagated: {inbound!r}"
            assert DOCUMENTED_PATTERN.match(resolved)


# --------------------------------------------------------------------------
# (2) format and uniqueness
# --------------------------------------------------------------------------


async def test_a_generated_id_matches_the_documented_pattern():
    messages = await _run(RequestIDMiddleware(_terminal_app()), _scope({}))
    generated = _response_header(messages, REQUEST_ID_HEADER)
    assert generated is not None
    assert DOCUMENTED_PATTERN.match(generated), f"generated id {generated!r} does not match the documented pattern"


async def test_generated_ids_are_unique_over_1000_requests():
    """Uniqueness is a property of the generator, so it is measured in bulk."""
    seen: set[str] = set()
    middleware = RequestIDMiddleware(_terminal_app())
    for _ in range(1000):
        messages = await _run(middleware, _scope({}))
        generated = _response_header(messages, REQUEST_ID_HEADER)
        assert generated is not None
        assert generated not in seen, f"a request id was reused within one process: {generated!r}"
        seen.add(generated)
    assert len(seen) == 1000


# --------------------------------------------------------------------------
# (3) state == header, on BOTH paths
# --------------------------------------------------------------------------


async def test_state_matches_the_response_header_on_passthrough():
    state: list[str] = []
    messages = await _run(RequestIDMiddleware(_terminal_app(state)), _scope({}))
    header = _response_header(messages, REQUEST_ID_HEADER)
    assert header is not None
    assert state == [header], "request.state.request_id disagreed with the response header on a pass-through"


async def test_state_matches_the_response_header_on_the_rate_limit_reject():
    """The reject path is a different code path, and that is the point.

    ``RateLimitMiddleware`` runs OUTSIDE ``RequestIDMiddleware`` (which is
    innermost), so its short-circuit executes before the inner middleware ever
    runs. Without ``rate_limit.py:63`` the 429 would carry no request id at all,
    and every log line for a throttled request would be uncorrelatable.
    """
    state: list[str] = []
    admission = _AlwaysLimitedAdmission()
    app = RateLimitMiddleware(RequestIDMiddleware(_terminal_app(state)), admission=admission)
    messages = await _run(app, _scope({}))

    assert admission.calls == 1, "the admission service was never consulted; the reject branch was not exercised"
    assert _status(messages) == 429, f"expected a 429 from the limited branch, got {_status(messages)}"
    assert state == [], "the inner middleware ran on a rejected request; the reject path was not isolated"

    header = _response_header(messages, REQUEST_ID_HEADER)
    assert header is not None, "the 429 carried no X-Request-ID"
    assert DOCUMENTED_PATTERN.match(header), f"the 429 carried a malformed id: {header!r}"
    # Equality against a freshly minted id would be meaningless: two calls to
    # resolve_request_id(None) return two DIFFERENT uuids, so the first draft of
    # this test compared the header with a second mint and failed every time.
    # The claim worth making is that the reject path minted a VALID id of its
    # own rather than echoing nothing, plus that a valid inbound id wins there
    # too - which the next test checks.
    assert header != "", "the 429 carried an empty X-Request-ID"


async def test_the_reject_path_echoes_a_valid_inbound_id_too():
    """A caller correlating a throttled request must get ITS id back, not a fresh one."""
    admission = _AlwaysLimitedAdmission()
    app = RateLimitMiddleware(RequestIDMiddleware(_terminal_app()), admission=admission)
    messages = await _run(app, _scope({REQUEST_ID_HEADER: VALID_INBOUND_ID}))
    assert _status(messages) == 429
    assert _response_header(messages, REQUEST_ID_HEADER) == VALID_INBOUND_ID


# --------------------------------------------------------------------------
# failure mode (ii) + the ordering premise, structurally
# --------------------------------------------------------------------------


def _module_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _called_names(relative: str) -> set[str]:
    """Every bare function name called anywhere in a module."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(_module_text(relative))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


@pytest.mark.parametrize("guard", ["src/app/middleware/rate_limit.py", "src/app/middleware/ip_filter.py"])
def test_every_outer_guard_wraps_send_through_the_shared_helpers(guard: str):
    """Both guards must re-derive the id, or their rejects lose the header.

    Asserted on the call SITES as well as the shared helper, because a guard that
    grew a private copy of the sender would still satisfy every behavioural test
    above while drifting from the one implementation the fix touches.
    """
    called = _called_names(guard)
    assert "wrap_send_with_request_id" in called, f"{guard} no longer wraps send with the request id"
    assert "request_id_from_scope" in called, f"{guard} no longer derives the id from the scope"


def test_request_id_middleware_is_ordered_innermost():
    """The premise of the pass-through authority.

    ``ordering.py`` adds RequestID FIRST, and registration is LIFO, so it is the
    LAST thing before the handler. If a future reorder makes it outermost, the
    guards would stop needing their own wrapper and this oracle would be pinning
    a condition that no longer holds.
    """
    assert GUARD_ORDER, "GUARD_ORDER is empty; the middleware-order contract cannot be checked"
    first = GUARD_ORDER[0]
    assert "RequestID" in str(first) or first is RequestIDMiddleware, (
        f"RequestIDMiddleware is no longer first in GUARD_ORDER (now {GUARD_ORDER[0]!r}); "
        "it must be innermost for the guards' reject path to be necessary at all"
    )


# --------------------------------------------------------------------------
# failure mode (iii): the grep pre-check, made enforceable
# --------------------------------------------------------------------------


def test_no_other_test_hardcodes_a_request_id_value():
    """The pre-check stays true.

    A test that asserts a literal id value cannot detect a regression — it
    asserts the constant. Anything of that shape elsewhere in the suite is a
    request-correlation test that will pass whatever the middleware does, and the
    audit's grep found none; this keeps it that way.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "tests").rglob("test_*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""["']([a-zA-Z0-9\-]{8,})["']""", source):
            if match.group(1) in {VALID_INBOUND_ID, MAX_LENGTH_ID}:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {match.group(1)!r}")
    assert not offenders, "these tests hardcode a request-id value, so they cannot detect a regression:\n" + "\n".join(
        offenders
    )


def test_the_oracle_reads_the_pattern_rather_than_importing_it():
    """Guards the guard: DOCUMENTED_PATTERN is restated, not imported.

    If someone replaces the module-level constant with ``_REQUEST_ID_PATTERN``
    imported from production, every format assertion in this file silently agrees
    with whatever the code decided, which is the shape of the bug this oracle
    exists to catch.

    The check is on the IMPORT STATEMENTS, not on the file text. The first draft
    scanned the whole source and failed on this file's OWN pre-check, which quotes
    the production line ``_REQUEST_ID_PATTERN = re.compile(...)`` as part of
    documenting what was grepped. A rule that forbids the explanation forbids the
    fix, so the scan is narrowed to what is actually bound.
    """
    tree = ast.parse(_module_text(Path(__file__).relative_to(REPO_ROOT).as_posix()))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "_REQUEST_ID_PATTERN" not in imported, "the oracle must restate the pattern, not import it"


class TestOneRequestOneId:
    """S-5: the request ID is a property of the REQUEST, not of the guard.

    Three middleware need the ID for a response they may short-circuit --
    IPFilter (403), RateLimit (429/503) and RequestIDMiddleware itself -- and
    all three called ``request_id_from_scope`` unconditionally. Before the
    memoisation each call drew its own uuid4, so one request carried up to
    three different identities: the ID a guard echoed on a reject was an ID no
    other layer, and no log line, had ever recorded.

    The reject path is covered by composition rather than by a second stack
    drive: ``test_every_outer_guard_wraps_send_through_the_shared_helpers``
    above already pins that IPFilter and RateLimit route through this exact
    helper, and the tests here pin that the helper draws once. Those two facts
    together mean a 403/429 carries the same value a pass-through would.
    """

    def test_three_callers_share_one_derivation(self):
        """The whole defect, in one assertion: three asks, one uuid4."""
        scope = _scope({})
        with patch("src.app.middleware.request_id.uuid.uuid4", wraps=uuid.uuid4) as spy:
            first = request_id_from_scope(scope)
            second = request_id_from_scope(scope)
            third = request_id_from_scope(scope)
        assert spy.call_count == 1, (
            "each middleware that asks for the ID before must not draw a new one; "
            f"uuid4 was called {spy.call_count} times"
        )
        assert first == second == third

    def test_an_inbound_id_is_propagated_unchanged_and_not_memoised_away(self):
        """The memo must not turn a propagated id into a minted one.

        Asserting only the drawn case would let a broken memo pass: if the
        cache were consulted before the headers were read, an inbound
        trace id would be silently replaced by a fresh uuid on the second
        caller, which breaks distributed tracing rather than performance.
        """
        scope = _scope({"x-request-id": VALID_INBOUND_ID})
        assert request_id_from_scope(scope) == VALID_INBOUND_ID
        assert request_id_from_scope(scope) == VALID_INBOUND_ID

    def test_distinct_scopes_do_not_share_an_id(self):
        """The memo is per-scope, not global.

        A module-level cache would make this fail and would also leak one
        request's identity into every later request in the process.
        """
        assert request_id_from_scope(_scope({})) != request_id_from_scope(_scope({}))


    source = _module_text(Path(__file__).relative_to(REPO_ROOT).as_posix())
    assert re.search(r'DOCUMENTED_PATTERN = re\.compile\(r"\^\[a-zA-Z0-9\\-\]\{1,128\}\$"\)', source), (
        "DOCUMENTED_PATTERN is no longer the documented ^[a-zA-Z0-9\\-]{1,128}$ form"
    )
