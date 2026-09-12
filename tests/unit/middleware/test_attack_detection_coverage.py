"""Unit tests for Attack Detection middleware (middleware/attack_detection.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

from src.app.middleware.attack_detection import AttackDetectionMiddleware, AttackType


def _make_request(method="GET", path="/", query="", headers=None, body=b""):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    request = MagicMock()
    request.url.path = path
    request.method = method
    request.query_params = query
    request.headers = Headers(headers or {})
    request.state = MagicMock(ip_whitelisted=False)

    chunks = [body] if body else []

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
        return {"type": "http.disconnect"}

    request.stream = MagicMock(return_value=_async_iter(chunks))
    # P0-1: _detect_attack_patterns reads via `await request.body()`
    # (Starlette replays it downstream); mock it alongside stream().
    request.body = AsyncMock(return_value=body)
    request.scope = scope
    return request


async def _async_iter(items):
    for item in items:
        yield item


@pytest.fixture
def middleware():
    with patch("src.app.modules.security.service.IPFilterService"):
        mw = AttackDetectionMiddleware(app=MagicMock())
    mw._enabled = True
    mw._log_full_request = False
    return mw


def test_sanitize_input_nfkc():
    payload = "ＵＮＩＯＮ ＳＥＬＥＣＴ"
    out = AttackDetectionMiddleware._sanitize_input(payload.encode())
    assert b"UNION" in out.upper()


def test_sanitize_input_binary_passthrough():
    bad = b"\xff\xfe\x00"
    out = AttackDetectionMiddleware._sanitize_input(bad)
    assert out == bad


def test_is_exempt_path(middleware):
    assert middleware._is_exempt_path("/health") is True
    assert middleware._is_exempt_path("/docs-attack") is False
    assert middleware._is_exempt_path("/docs/foo") is True


def test_check_for_patterns_sql(middleware):
    attacks: list[dict] = []
    middleware._check_for_patterns(b"x WHERE 1=1 OR 1=1", "url_path", attacks)
    assert any(a["attack_type"] == AttackType.SQL_INJECTION.value for a in attacks)


def test_check_for_patterns_xss(middleware):
    attacks: list[dict] = []
    middleware._check_for_patterns(b"<script>alert(1)</script>", "url_path", attacks)
    assert any(a["attack_type"] == AttackType.XSS.value for a in attacks)


async def test_detect_attack_patterns_body(middleware):
    req = _make_request(method="POST", path="/login", body=b"<script>alert(1)</script>")
    attacks, new_req = await middleware._detect_attack_patterns(req)
    assert any(a["location"] == "request_body" for a in attacks)


async def test_detect_attack_patterns_query(middleware):
    req = _make_request(method="GET", path="/x", query="q=1' OR '1'='1")
    attacks, _ = await middleware._detect_attack_patterns(req)
    assert any(a["attack_type"] == AttackType.SQL_INJECTION.value for a in attacks)


async def test_dispatch_disabled_passes_through(middleware):
    middleware._enabled = False
    called = {}

    async def app(request):
        called["yes"] = True
        return MagicMock()

    req = _make_request(path="/anything")
    await middleware.dispatch(req, app)
    assert called.get("yes")


async def test_dispatch_exempt_path(middleware):
    called = {}

    async def app(request):
        called["yes"] = True
        return MagicMock()

    req = _make_request(path="/health")
    await middleware.dispatch(req, app)
    assert called.get("yes")


async def test_dispatch_whitelisted(middleware):
    called = {}

    async def app(request):
        called["yes"] = True
        return MagicMock()

    req = _make_request(path="/login")
    req.state.ip_whitelisted = True
    await middleware.dispatch(req, app)
    assert called.get("yes")


async def test_dispatch_block_mode(middleware):
    fake_settings = MagicMock(
        ATTACK_DETECTION_MODE="block",
        ATTACK_DETECTION_ENABLED=True,
        ATTACK_DETECTION_LOG_FULL_REQUEST=False,
    )
    with (
        patch("src.app.middleware.attack_detection.settings", fake_settings),
        patch("src.app.core.security.logging.log_security_event"),
        patch("src.app.core.security.logging.create_security_event", return_value={}),
    ):
        req = _make_request(method="POST", path="/login", body=b"<script>x</script>")
        response = await middleware.dispatch(req, AsyncMock())
    assert response.status_code == 400


async def test_dispatch_ban_mode(middleware):
    fake_filter = MagicMock()
    fake_filter.add_to_blacklist = AsyncMock()
    fake_attack = MagicMock(
        ATTACK_DETECTION_MODE="ban",
        ATTACK_DETECTION_ENABLED=True,
        ATTACK_DETECTION_LOG_FULL_REQUEST=False,
    )
    fake_settings = MagicMock(attack_detection=fake_attack)
    with (
        patch("src.app.middleware.attack_detection.settings", fake_settings),
        patch("src.app.core.security.logging.log_security_event"),
        patch("src.app.core.security.logging.create_security_event", return_value={}),
    ):
        middleware._filter_service = fake_filter
        req = _make_request(method="POST", path="/login", body=b"<script>x</script>")
        response = await middleware.dispatch(req, AsyncMock())
    assert response.status_code == 403
    fake_filter.add_to_blacklist.assert_awaited_once()


async def test_dispatch_no_attack_passes_through(middleware):
    called = {}

    async def app(request):
        called["yes"] = True
        return MagicMock()

    req = _make_request(method="GET", path="/home")
    await middleware.dispatch(req, app)
    assert called.get("yes")


def test_body_passthrough_end_to_end():
    """P0-1 regression: middleware must not consume the request body.

    Previously ``_detect_attack_patterns`` drained ``request.stream()`` and
    rebuilt a ``Request`` object that Starlette's ``BaseHTTPMiddleware``
    discarded (``call_next`` ignores its argument), so every POST/PUT/PATCH
    handler received ``b""``. Now the body is read via ``Request.body()``,
    which Starlette's ``_CachedRequest`` replays downstream.
    """
    import json as _json

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def echo(request):
        body = await request.body()
        return JSONResponse({"body": body.decode("utf-8")})

    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    app.add_middleware(AttackDetectionMiddleware)
    client = TestClient(app)
    response = client.post("/echo", json={"hello": "world"})
    assert response.status_code == 200
    assert _json.loads(response.json()["body"]) == {"hello": "world"}


class TestP17RegexTightening:
    """P1-7: tightened xss_event / sql_comment must catch real payloads and
    ignore benign inputs. Runs against the production COMBINED_ATTACK_RE via
    the middleware's own _check_for_patterns seam (not the stale test mirror).
    """

    @pytest.mark.parametrize(
        "payload",
        [
            b"<img src=x onerror=alert(1)>",
            b"<svg/onload=alert(1)>",
            b"<body onload=evil()>",
            b"admin'--",
            b"1; DROP TABLE x; --",
        ],
    )
    def test_true_positives_still_detected(self, middleware, payload: bytes) -> None:
        attacks: list[dict] = []
        middleware._check_for_patterns(payload, "request_body", attacks)
        assert attacks, f"missed real attack payload: {payload!r}"

    @pytest.mark.parametrize(
        "payload",
        [
            b"only=5&sort=asc",  # bare on*= query/form param, no tag context
            b"<div>price only=$5</div>",  # on-word outside any tag
            b"data-only=x",  # hyphenated attr name, not an event handler
            b"em-dash note -- done",  # prose double-dash, no SQL context
            b"select an option from the list -- thanks",  # keywords, no quote/semicolon
            b"--boundary1234567890",  # multipart boundary
        ],
    )
    def test_false_positives_ignored(self, middleware, payload: bytes) -> None:
        attacks: list[dict] = []
        middleware._check_for_patterns(payload, "request_body", attacks)
        assert not attacks, f"false positive on benign input: {payload!r} -> {attacks}"
