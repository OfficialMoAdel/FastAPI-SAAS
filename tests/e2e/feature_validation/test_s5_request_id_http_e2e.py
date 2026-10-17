"""S-5 e2e: request ID is one identity across the real HTTP stack.

The unit oracle (``tests/unit/middleware/test_request_id_propagation.py``)
pins the contract on a bare harness: a valid inbound ``X-Request-ID`` is
echoed, an invalid one is replaced, the generated value matches the
documented pattern and is unique over 1000 requests, and
``request.state.request_id`` equals the response header on both the
pass-through and the 429 reject path. What it cannot pin is the thing that
actually regressed: the three middleware that need the ID each derived
their own uuid4, so a single request carried three different values. That
is a property of the WIRING -- which middleware is innermost, and whether
the outer guards route through the shared helpers -- and it only shows up
over the real stack.

This file is that missing HTTP leg. It drives the real app (ASGITransport
over ``src.app.main.app``, with the full guard order) and asserts on the
wire:

1. a valid inbound ``X-Request-ID`` is echoed unchanged (zero-change case);
2. no inbound header -> the documented format, unique over 1000 requests;
3. a malformed inbound header is REPLACED, not echoed, and matches the
   format (the log-injection guard);
4. the exempt ``/api/v1/health`` path still mints an ID (IPFilter mints
   before its exempt-path check);
5. the real 429 reject path (the auth rate-limit budget on
   ``GET /api/v1/account/sso/google/authorize``) carries an ``X-Request-ID``
   that matches ``request.state.request_id``.

The pre-check the bead requires -- a sweep for hardcoded request-id
assertions across tests and scripts -- runs FIRST and its output is logged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

import src.app.middleware.request_id as request_id_module

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Restated from the oracle, not imported: the oracle must be readable
#: without executing the module it checks.
DOCUMENTED_PATTERN = re.compile(r"^[a-zA-Z0-9\-]{1,128}$")

VALID_INBOUND_ID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
MALFORMED_ID = "bad id!!"
HEALTH_PATH = "/api/v1/health"
SSO_AUTHORIZE_PATH = "/api/v1/account/sso/google/authorize"
AUTH_BUDGET_ATTEMPTS = 5
UNIQUENESS_REQUESTS = 1000

pytestmark = pytest.mark.e2e


def _sweep() -> list[tuple[str, int, str]]:
    """Find hardcoded request-id/trace literals in tests/ and scripts/.

    A hit is a line that mentions a request-id/trace token AND carries a
    quoted literal of at least 8 characters. The invariant this pins is
    that no hit is an assertion on the middleware's OUTPUT -- a test that
    asserts a literal request id would silently break the moment the
    middleware changes how it derives one.
    """
    token = re.compile(r"request[-_]?id|trace[-_]?id|correlation", re.IGNORECASE)
    literal = re.compile(r"""["'][A-Za-z0-9\-_.:=<>/@]{8,}["']""")
    hits: list[tuple[str, int, str]] = []
    for root in ("tests", "scripts"):
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                if token.search(line) and literal.search(line):
                    hits.append((path.relative_to(REPO_ROOT).as_posix(), lineno, line.strip()))
    return hits


async def test_precheck_grep_sweep_is_logged(client: AsyncClient) -> None:
    """Run the bead's pre-check sweep FIRST and log its output as evidence.

    The sweep is deliberately BROAD: it logs every line in tests/ and
    scripts/ that mentions a request-id/trace token and carries a quoted
    literal of >=8 characters. Most hits are legitimate -- a test that
    supplies its OWN inbound ``X-Request-ID`` and asserts it is echoed is
    testing the contract, not pinning the middleware's derivation. The
    invariant that would actually catch a silent break (no test asserting a
    hardcoded GENERATED id) is enforced by the unit oracle's
    ``test_no_other_test_hardcodes_a_request_id_value``; this test's job is
    to run the sweep, prove it is non-vacuous, and attach its output.
    """
    hits = _sweep()
    for relpath, lineno, line in hits:
        print(f"SWEEP {relpath}:{lineno}: {line}")
    assert hits, "the sweep must actually scan tests/ and scripts/ (non-vacuous)"


async def test_valid_inbound_request_id_is_echoed_unchanged(client: AsyncClient) -> None:
    response = await client.get(HEALTH_PATH, headers={"X-Request-ID": VALID_INBOUND_ID})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == VALID_INBOUND_ID


async def test_missing_inbound_id_is_generated_and_unique_over_1000(
    client: AsyncClient,
) -> None:
    seen: set[str] = set()
    for _ in range(UNIQUENESS_REQUESTS):
        response = await client.get(HEALTH_PATH)
        request_id = response.headers["X-Request-ID"]
        assert DOCUMENTED_PATTERN.match(request_id), request_id
        seen.add(request_id)
    assert len(seen) == UNIQUENESS_REQUESTS, f"{len(seen)} distinct ids over {UNIQUENESS_REQUESTS}"


async def test_malformed_inbound_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get(HEALTH_PATH, headers={"X-Request-ID": MALFORMED_ID})
    request_id = response.headers["X-Request-ID"]
    assert request_id != MALFORMED_ID
    assert DOCUMENTED_PATTERN.match(request_id), request_id


async def test_health_exempt_path_still_mints_a_request_id(client: AsyncClient) -> None:
    response = await client.get(HEALTH_PATH)
    assert response.status_code == 200
    assert DOCUMENTED_PATTERN.match(response.headers["X-Request-ID"])


async def test_429_reject_path_carries_request_id_matching_request_state(
    client: AsyncClient, clear_auth_rate_limits
) -> None:
    await clear_auth_rate_limits()

    observed: list[str] = []
    original = request_id_module.set_request_id

    def recording_set_request_id(state: Any, value: str) -> None:
        observed.append(str(value))
        original(state, value)

    request_id_module.set_request_id = recording_set_request_id  # type: ignore[assignment]
    try:
        last_response = None
        for _ in range(AUTH_BUDGET_ATTEMPTS + 1):
            last_response = await client.get(SSO_AUTHORIZE_PATH)
    finally:
        request_id_module.set_request_id = original  # type: ignore[assignment]

    assert last_response is not None
    assert last_response.status_code == 429, last_response.status_code
    request_id = last_response.headers["X-Request-ID"]
    assert DOCUMENTED_PATTERN.match(request_id), request_id
    assert observed, "RequestIDMiddleware must have run and set request.state.request_id"
    assert request_id in observed, (request_id, observed)
