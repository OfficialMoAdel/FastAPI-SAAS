"""Unit tests for Attack Detection middleware (middleware/attack_detection.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.middleware.attack_detection import AttackDetectionMiddleware, AttackType


def _make_scope(method="GET", path="/", query="", headers=None, body=b"", state=None):
    """Build a native-ASGI (scope, receive, send, sent, downstream_app, downstream) tuple.

    P1-6: the middleware is native ASGI now — tests drive __call__ directly
    instead of BaseHTTP dispatch() with a mocked Request.
    """
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": dict(state or {}),
    }
    chunks = [body] if body else []

    async def receive():
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    downstream: dict = {}

    async def downstream_app(scope, receive, send):
        downstream["called"] = True
        # Drain any body so replay bugs surface as empty reads here.
        seen = b""
        while True:
            message = await receive()
            seen += message.get("body", b"")
            if not message.get("more_body", False):
                break
        downstream["body"] = seen

    return scope, receive, send, sent, downstream_app, downstream


def _status_of(sent: list[dict]) -> int | None:
    for message in sent:
        if message["type"] == "http.response.start":
            return message["status"]
    return None


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


async def test_scan_request_body(middleware):
    scope, receive, _, _, _, _ = _make_scope(method="POST", path="/login", body=b"<script>alert(1)</script>")
    attacks, _ = await middleware._scan_request(
        scope["path"], scope["query_string"], scope["headers"], scope["method"], receive
    )
    assert any(a["location"] == "request_body" for a in attacks)


async def test_scan_request_query(middleware):
    scope, receive, _, _, _, _ = _make_scope(method="GET", path="/x", query="q=1' OR '1'='1")
    attacks, _ = await middleware._scan_request(
        scope["path"], scope["query_string"], scope["headers"], scope["method"], receive
    )
    assert any(a["attack_type"] == AttackType.SQL_INJECTION.value for a in attacks)


async def test_call_disabled_passes_through(middleware):
    middleware._enabled = False
    scope, receive, send, _, downstream_app, downstream = _make_scope(path="/anything")
    middleware.app = downstream_app
    await middleware(scope, receive, send)
    assert downstream.get("called")


async def test_call_exempt_path(middleware):
    scope, receive, send, _, downstream_app, downstream = _make_scope(path="/health")
    middleware.app = downstream_app
    await middleware(scope, receive, send)
    assert downstream.get("called")


async def test_call_whitelisted(middleware):
    scope, receive, send, _, downstream_app, downstream = _make_scope(
        path="/login", state={"ip_whitelisted": True}
    )
    middleware.app = downstream_app
    await middleware(scope, receive, send)
    assert downstream.get("called")


async def test_call_block_mode(middleware):
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
        scope, receive, send, sent, _, _ = _make_scope(
            method="POST", path="/login", body=b"<script>x</script>"
        )
        await middleware(scope, receive, send)
    assert _status_of(sent) == 400


async def test_call_ban_mode(middleware):
    fake_filter = MagicMock()
    fake_filter.ban_ip = AsyncMock()
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
        scope, receive, send, sent, _, _ = _make_scope(
            method="POST", path="/login", body=b"<script>x</script>"
        )
        await middleware(scope, receive, send)
    assert _status_of(sent) == 403
    fake_filter.ban_ip.assert_awaited_once()


async def test_call_no_attack_passes_through(middleware):
    scope, receive, send, _, downstream_app, downstream = _make_scope(path="/home")
    middleware.app = downstream_app
    await middleware(scope, receive, send)
    assert downstream.get("called")


async def test_call_replays_body_downstream(middleware):
    """P1-6: native drain must replay the body — downstream must see it whole."""
    scope, receive, send, _, downstream_app, downstream = _make_scope(
        method="POST", path="/echo", body=b'{"hello": "world"}'
    )
    middleware.app = downstream_app
    await middleware(scope, receive, send)
    assert downstream.get("called")
    assert downstream.get("body") == b'{"hello": "world"}'


def test_body_passthrough_end_to_end():
    """P0-1 regression: middleware must not consume the request body.

    Previously ``_detect_attack_patterns`` drained ``request.stream()`` and
    rebuilt a ``Request`` object that Starlette's ``BaseHTTPMiddleware``
    discarded (``call_next`` ignores its argument), so every POST/PUT/PATCH
    handler received ``b""``. Now the body is drained once and replayed
    downstream via a wrapped receive channel (P1-6 native ASGI).
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


@pytest.mark.timeout(60)  # Prevent hanging in full test suite
def test_streaming_response_not_starved_by_replay_channel():
    """P1-6 follow-up: post-replay receive() must delegate to the live channel.

    The first replay implementation returned empty ``http.request`` forever.
    ``StreamingResponse.listen_for_disconnect`` then busy-looped on instant
    coroutines, starving the streamer task of the event loop — observed as a
    hang in the AI chat-stream integration test. Now the replay delegates to
    the underlying channel after the first read, so the listener parks on the
    real channel and the streamer runs to completion.
    """
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def sse(request):
        body = await request.body()
        assert body  # replay still delivers the full body downstream

        async def generate():
            yield b"data: one\n\n"
            yield b"data: two\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/sse", sse, methods=["POST"])])
    app.add_middleware(AttackDetectionMiddleware)
    client = TestClient(app)
    response = client.post("/sse", json={"hello": "world"})
    assert response.status_code == 200
    assert "data: one" in response.text
    assert "data: two" in response.text


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
