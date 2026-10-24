"""F-4 differential: the threaded GZip middleware is byte-identical.

WHAT THIS PROVES
    ``ThreadedGZipMiddleware`` moves Starlette's deflate off the event loop
    (F-4: 1.315 ms / 256 KB, 5.508 ms / 1 MB of loop stall, and zlib releases
    the GIL so the thread actually helps). It must not change a single byte
    of the response. This file runs the STOCK ``GZipMiddleware`` and the
    threaded one over the same ASGI app and asserts the wire bytes, the
    status and the headers agree, across the normal, streaming,
    ``minimum_size`` and ``content-encoding``-preset paths.

HOW THE WIRE BYTES ARE OBTAINED
    ``httpx`` decodes ``content-encoding`` transparently, so ``response.content``
    is the DECOMPRESSED payload. The undecoded bytes come from
    ``iter_raw()`` on a streamed response.

WHY THE mtime IS NORMALISED
    ``gzip.GzipFile`` stamps the current time into the 10-byte gzip header, so
    two runs are not byte-identical by construction. Bytes 4:8 are that
    mtime; the comparison starts at byte 10 (magic, method, flags, mtime,
    xfl, os), and the decompressed payload is compared separately -- so a
    real difference in the deflate stream or the framing still fails.

THE DRIFT RATCHET
    The responder is a subclass whose ``send_with_gzip`` mirrors upstream
    Starlette 0.40.0 with three write sites moved into an executor.
    ``test_the_upstream_responder_still_has_the_shape_we_forked`` fails when
    upstream moves a write site, forcing a re-check rather than letting the
    fork silently diverge.
"""

from __future__ import annotations

import gzip
import inspect

import pytest
from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.app.middleware.gzip_threaded import ThreadedGZipMiddleware, ThreadedGZipResponder

PAYLOADS = {
    "tiny": b"x" * 10,  # below minimum_size -> passthrough
    "at_minimum": b"y" * 500,
    "just_over": b"y" * 501,
    "five_kb": bytes(range(256)) * 20,
    "one_mb": b"z" * (1024 * 1024),
}

#: gzip header is 10 bytes: magic(2) method(1) flags(1) mtime(4) xfl(1) os(1).
_GZIP_HEADER = 10


def _app(middleware_cls: type, *, body: bytes, streaming: bool, preset_encoding: str | None = None) -> Starlette:
    def endpoint(request):  # type: ignore[no-untyped-def]
        if preset_encoding is not None:
            return Response(body, media_type="text/plain", headers={"Content-Encoding": preset_encoding})
        if streaming:

            async def chunks():  # type: ignore[no-untyped-def]
                for i in range(0, len(body), 4096):
                    yield body[i : i + 4096]

            return StreamingResponse(chunks(), media_type="text/plain")
        return PlainTextResponse(body)

    app = Starlette(routes=[Route("/x", endpoint)])
    app.add_middleware(middleware_cls, minimum_size=500, compresslevel=4)
    return app


def _get(app: Starlette) -> tuple[int, dict[bytes, bytes], bytes, bytes]:
    """Return (status, headers, raw wire bytes, decoded bytes)."""
    with TestClient(app) as client:
        # An explicit Accept-Encoding is still sent, so the middleware decides
        # to compress exactly as it would in production. ``iter_raw`` is the
        # ONLY consumer, because httpx marks the stream consumed after one
        # pass; the decoded view is produced here instead.
        with client.stream("GET", "/x", headers={"Accept-Encoding": "gzip"}) as response:
            raw = b"".join(response.iter_raw())
            headers = dict(response.headers.raw)
            decoded = gzip.decompress(raw) if headers.get(b"content-encoding") == b"gzip" else raw
            return response.status_code, headers, raw, decoded


def _assert_identical(body: bytes, *, streaming: bool = False, preset: str | None = None, expect_gzip: bool) -> None:
    stock = _get(_app(GZipMiddleware, body=body, streaming=streaming, preset_encoding=preset))
    threaded = _get(_app(ThreadedGZipMiddleware, body=body, streaming=streaming, preset_encoding=preset))

    assert threaded[0] == stock[0]
    assert threaded[1] == stock[1], "framing headers differ (Content-Length/Encoding/Vary)"
    assert threaded[3] == stock[3] == body, "decoded payload differs"
    # Not a tautology: pin that the case under test really is (or is not)
    # compressed, so a middleware that silently stopped compressing fails.
    assert (stock[1].get(b"content-encoding") == b"gzip") is expect_gzip
    if expect_gzip:
        # Same deflate stream and trailer; the mtime in the gzip header is the
        # only field that is not reproducible.
        assert threaded[2][_GZIP_HEADER:] == stock[2][_GZIP_HEADER:]
    else:
        assert threaded[2] == stock[2] == body


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_output_is_identical_to_stock_gzip(name: str) -> None:
    _assert_identical(PAYLOADS[name], expect_gzip=name != "tiny")


@pytest.mark.parametrize("name", ["five_kb", "one_mb"])
def test_streaming_output_is_identical(name: str) -> None:
    _assert_identical(PAYLOADS[name], streaming=True, expect_gzip=True)


def test_preset_content_encoding_passes_through_identically() -> None:
    _assert_identical(PAYLOADS["five_kb"], preset="identity", expect_gzip=False)


def test_the_upstream_responder_still_has_the_shape_we_forked() -> None:
    """Drift ratchet: upstream 0.40.0 has exactly three write sites."""
    assert issubclass(ThreadedGZipResponder, GZipResponder)
    assert issubclass(ThreadedGZipMiddleware, GZipMiddleware)
    source = inspect.getsource(GZipResponder.send_with_gzip)
    assert source.count("self.gzip_file.write(") == 3, "upstream moved a write site; re-fork ThreadedGZipResponder"
    assert source.count('message["body"] =') == 3, "upstream changed the body-rewrite sites; re-fork"


def test_the_deflate_runs_off_the_request_thread() -> None:
    """F-4's whole point: the compression is NOT on the event-loop thread.

    Deterministic (a thread NAME), not a timing assertion: the deflate must
    run on the dedicated ``gzip_*`` pool. A regression that moves the work
    back inline (or offloads to a no-op) fails here.
    """
    import threading

    seen: list[str] = []

    class _ProbeResponder(ThreadedGZipResponder):
        def _deflate_all(self, body: bytes) -> None:
            seen.append(threading.current_thread().name)
            super()._deflate_all(body)

    class _ProbeMiddleware(ThreadedGZipMiddleware):
        responder_class = _ProbeResponder

    def endpoint(request):  # type: ignore[no-untyped-def]
        return PlainTextResponse(PAYLOADS["five_kb"])

    app = Starlette(routes=[Route("/x", endpoint)])
    app.add_middleware(_ProbeMiddleware, minimum_size=500, compresslevel=4)
    _get(app)

    assert seen, "the compression step never ran"
    assert all(name.startswith("gzip") for name in seen), f"deflate ran on {seen}"
