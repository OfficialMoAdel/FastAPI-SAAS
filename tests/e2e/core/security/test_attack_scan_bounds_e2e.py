"""nrgi.3 e2e: the scan bounds must not cost a single detection.

Bead ``FastAPI_saas-nrgi.3`` asked to bound the attack-detection body scan.
This file is the counter-test for whatever bound was chosen — it is the
assertion that stops a future "scan the first N bytes" optimisation from
slipping in unnoticed:

1. A malicious payload in a POST body is still detected at 0%, 25%, 50%,
   75%, 90%, 99% and 100% depth through a large body. A truncation at ANY
   depth fails here.
2. The scanned body is never truncated: the bytes handed downstream are
   byte-identical to the bytes sent, at every depth and either side of
   ``_MAX_SCAN_SIZE``.
3. The 10 MB body limit still applies and still returns 413 — with the
   attack-scan middleware in the stack, so the two bounds are proven to
   coexist and size wins over the scan.
4. The GET/HEAD skip (see tests/unit/middleware/test_attack_scan_method_gate.py)
   removes no coverage: query strings, paths and headers are still scanned.

Real middleware stack (guard order per src/app/core/guard/ordering.py),
driven through a genuine ASGI scope — no mocks on the request path.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.app.core.config import settings
from src.app.middleware import attack_detection as ad
from src.app.middleware.attack_detection import AttackDetectionMiddleware
from src.app.middleware.body_limit import DEFAULT_MAX_BODY_SIZE, RequestBodyLimitMiddleware

MAX_SCAN_SIZE = AttackDetectionMiddleware._MAX_SCAN_SIZE
MAX_BODY_SIZE = settings.app.MAX_BODY_SIZE

SQLI = b"UNION SELECT password FROM users"

#: One representative per AttackType. Chosen to survive real JSON bodies.
FAMILIES = {
    "sqli": SQLI,
    "xss": b"<script>alert(1)</script>",
    "traversal": b"../../etc/passwd",
    "cmd_injection": b"; cat /etc/passwd",
    "ssrf": b"http://169.254.169.254/latest/meta-data/",
}


# --- a minimal app: BodyLimit (outer) -> AttackDetection (inner) ------------


async def _echo(request):
    """Return the body verbatim so any truncation in the stack shows up."""
    return JSONResponse({"body": (await request.body()).decode("latin-1")})


async def _ok(request):
    return JSONResponse({"ok": True})


def _build_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/files", _echo, methods=["POST", "PUT", "PATCH"]),
            Route("/users", _ok, methods=["GET", "HEAD"]),
        ]
    )
    # add_middleware is LIFO: last added runs first on request.
    app.add_middleware(AttackDetectionMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY_SIZE)
    return app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """TestClient over the real stack, in block mode, with Redis rules stubbed."""
    from src.app.core.guard.ip_rules import RedisIpRules

    monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_MODE", "block", raising=False)
    monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_LOG_FULL_REQUEST", False, raising=False)
    monkeypatch.setattr(settings.attack_detection, "ATTACK_DETECTION_ENABLED", True, raising=False)

    def _no_redis(self, *args: Any, **kwargs: Any) -> None:
        self._status_cache = {}

    monkeypatch.setattr(RedisIpRules, "__init__", _no_redis, raising=False)

    with TestClient(_build_app(), raise_server_exceptions=False) as c:
        yield c



def _body_with_payload_at(total: int, payload: bytes, depth_pct: int) -> bytes:
    """A JSON body of exactly ``total`` bytes carrying ``payload``.

    ``depth_pct`` is where the payload sits between the head padding and
    the tail padding — 99 puts it within the last 1% of the body.
    """
    assert total > len(payload) + 256, "body must be large enough to hold padding"
    template = b'{"pad":"{PAD}","q":"{PAYLOAD}","tail":"{TAIL}"}'
    fixed = len(template) - len("{PAD}{PAYLOAD}{TAIL}") + len(payload)
    fill = total - fixed
    assert fill > 0, (total, fixed)
    # Keep at least one byte of tail so the body stays valid JSON at 100%;
    # a schema 422 would mask the scan verdict this test is asserting.
    pad_len = min(int(fill * depth_pct / 100), fill - 1)
    return (
        template.replace(b"{PAD}", b"x" * pad_len)
        .replace(b"{TAIL}", b"y" * (fill - pad_len))
        .replace(b"{PAYLOAD}", payload)
    )


def _post(client: TestClient, body: bytes):
    return client.post("/files", content=body, headers={"content-type": "application/json"})


# --- 1. detection at every depth (the truncation tripwire) ------------------


@pytest.mark.parametrize("depth_pct", [0, 25, 50, 75, 90, 99, 100])
def test_malicious_payload_detected_at_every_depth(client, depth_pct: int) -> None:
    """A payload near the END of a large body must still be caught.

    ``99`` and ``100`` are the ones that matter: any "scan only the first
    N bytes" optimisation fails here while passing every other case.
    """
    total = MAX_SCAN_SIZE - 4096
    body = _body_with_payload_at(total, SQLI, depth_pct)
    offset = body.index(SQLI)
    # Only the deep cases carry the "truncation would miss this" claim;
    # the shallow ones exist to prove the range is uniformly covered.
    if depth_pct >= 99:
        assert offset > total * 0.95, (
            f"payload at byte {offset} of {total} is not deep enough for the "
            f"truncation tripwire to mean anything"
        )

    response = _post(client, body)

    assert response.status_code == 400, (
        f"payload at byte {offset} ({depth_pct}% deep, len={len(body)}) was NOT blocked: "
        f"{response.status_code} {response.text[:200]}"
    )
    assert "attack pattern detected" in response.json()["error"]["message"].lower(), (
        "expected the attack scan's rejection, not a schema-validation error — "
        "the test payload must remain valid JSON"
    )


@pytest.mark.parametrize("family,payload", sorted(FAMILIES.items()))
def test_every_attack_family_still_detected_deep_in_a_large_body(
    client, family: str, payload: bytes
) -> None:
    """Not just SQLi — one representative per AttackType, deep in the body."""
    body = _body_with_payload_at(MAX_SCAN_SIZE - 4096, payload, 80)
    response = _post(client, body)
    assert response.status_code == 400, f"{family} ({payload[:30]!r}) escaped at 80% depth"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_every_body_method_is_still_scanned(client, method: str) -> None:
    body = _body_with_payload_at(MAX_SCAN_SIZE - 4096, SQLI, 90)
    response = client.request(method, "/files", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 400, f"{method} body was not scanned"


# --- 2. the body is never truncated -----------------------------------------


@pytest.mark.parametrize("depth_pct", [0, 50, 99])
def test_clean_body_reaches_downstream_byte_identical(client, depth_pct: int) -> None:
    """What the app reads must equal what the client sent — no short reads."""
    body = _body_with_payload_at(MAX_SCAN_SIZE - 4096, b"harmless value here", depth_pct)
    response = _post(client, body)

    assert response.status_code == 200, response.text[:200]
    assert response.json()["body"].encode("latin-1") == body


@pytest.mark.parametrize("size", [16, 1024, 65536, MAX_SCAN_SIZE - 1, MAX_SCAN_SIZE, MAX_SCAN_SIZE + 4096])
def test_body_size_is_never_altered(client, size: int) -> None:
    """Round-trip length is exact across the whole scannable range.

    Covers the boundaries either side of ``_MAX_SCAN_SIZE``, where the
    middleware switches between "scan it" and "stop buffering".
    """
    shell = b'{"pad":""}'
    body = b'{"pad":"' + b"a" * (size - len(shell)) + b'"}'
    assert len(body) == size
    response = _post(client, body)
    assert response.status_code == 200, response.text[:200]
    assert response.json()["body"].encode("latin-1") == body


def test_body_between_max_scan_and_10mb_is_delivered_whole(client) -> None:
    """Bodies above ``_MAX_SCAN_SIZE`` skip the PATTERN SCAN but are not cut off.

    This is the boundary a truncation fix would silently cross: the policy
    is "do not scan it", never "cut it short".
    """
    size = 2 * 1024 * 1024
    body = b'{"pad":"' + b"a" * (size - 10) + b'"}'
    assert MAX_SCAN_SIZE < len(body) < MAX_BODY_SIZE
    response = _post(client, body)
    assert response.status_code == 200, response.text[:200]
    assert response.json()["body"].encode("latin-1") == body


def test_multipart_upload_beyond_max_scan_is_delivered_whole(client) -> None:
    """A 2 MB multipart upload must survive the drain/replay intact."""
    boundary = b"----nrgi3boundary"
    blob = b"z" * (2 * 1024 * 1024)
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="a.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + blob + b"\r\n--" + boundary + b"--\r\n"
    )
    response = client.post(
        "/files",
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={boundary.decode()}"},
    )
    assert response.status_code == 200, response.text[:200]
    assert response.json()["body"].encode("latin-1") == body


# --- 3. the 10 MB body limit still applies ----------------------------------


def test_max_body_size_is_10mb() -> None:
    assert MAX_BODY_SIZE == 10 * 1024 * 1024 == DEFAULT_MAX_BODY_SIZE


def test_body_over_10mb_still_returns_413(client) -> None:
    """The hard cap is unregressed, with the attack scan in the stack."""
    body = b'{"pad":"' + b"a" * (MAX_BODY_SIZE + 1024) + b'"}'
    response = _post(client, body)

    assert response.status_code == 413, (
        f"expected the body limit's 413, got {response.status_code}: {response.text[:200]}"
    )
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["x-max-body-size"] == str(MAX_BODY_SIZE)


def test_oversized_body_with_a_payload_is_413_not_400(client) -> None:
    """A 10 MB+ SQLi body is a 413 (size wins), never a 400 from the scan."""
    body = b'{"pad":"' + b"a" * (MAX_BODY_SIZE + 1024) + b'","q":"' + SQLI + b'"}'
    response = _post(client, body)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_body_just_under_10mb_is_accepted(client) -> None:
    """The cap's boundary: comfortably under passes."""
    body = b'{"pad":"' + b"a" * (MAX_BODY_SIZE - 64) + b'"}'
    assert len(body) < MAX_BODY_SIZE
    response = _post(client, body)
    assert response.status_code == 200, response.text[:200]


# --- 4. the method-gated skip removes no GET coverage -----------------------


@pytest.mark.parametrize(
    "qs",
    [
        "file=../../etc/passwd",        # path traversal, literal
        "file=%2e%2e%2fetc%2fpasswd",   # path traversal, percent-encoded
        "cmd=x;cat+/etc/passwd",        # command injection
        "url=javascript:alert(1)",      # XSS javascript: URI
        "url=http://169.254.169.254/",  # SSRF cloud metadata
    ],
)
def test_get_attack_surface_is_still_scanned(client, qs: str) -> None:
    """GET skips the BODY scan; its query string is scanned exactly as before."""
    response = client.get(f"/users?{qs}")
    assert response.status_code == 400, (
        f"attack in a GET query string escaped the body-scan skip: {qs} -> {response.status_code}"
    )


def test_get_ssrf_header_is_still_blocked(client) -> None:
    response = client.get("/users", headers={"referer": "http://169.254.169.254/latest/meta-data/"})
    assert response.status_code == 400, "SSRF in a header escaped the body-scan skip"


def test_clean_get_is_unaffected(client) -> None:
    response = client.get("/users?page=1&limit=50")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_head_is_unaffected(client) -> None:
    assert client.head("/users").status_code == 200


# --- 5. the skip stays observable -------------------------------------------


def test_get_skip_is_logged_at_debug(client) -> None:
    """The skip the bead asked for is visible to an operator at DEBUG.

    Spied at the module logger's ``debug`` call rather than captured with
    ``structlog.testing.capture_logs()``. That helper swaps the GLOBAL processor
    chain, and a logger already bound by an earlier test in the session does not
    necessarily pick the swap up — which is why this test passed alone and
    returned an empty list in a full run.
    """
    calls: list[tuple[tuple, dict]] = []
    levels: list[int] = []

    def spy(event, *args, **kwargs):
        calls.append(((event, *args), kwargs))

    def guard(level):
        levels.append(level)
        return True

    with (
        patch.object(ad.logger, "is_enabled_for", side_effect=guard),
        patch.object(ad.logger, "debug", side_effect=spy),
    ):
        client.get("/users?page=1")

    skips = [c for c in calls if "skipped_body_scan" in str(c[0][0])]
    assert len(skips) == 1, f"expected one skip event, got {calls}"
    assert levels == [logging.DEBUG], f"skip was gated on {levels}, not DEBUG"
    assert skips[0][1]["method"] == "GET"


def test_post_does_not_log_a_skip(client) -> None:
    with structlog.testing.capture_logs() as captured:
        _post(client, b'{"a": 1}')
    assert not [r for r in captured if "skipped_body_scan" in r.get("event", "")]
