"""The body-limit middleware rejects oversized bodies BEFORE schema parsing.

Two independent bounds guard the passkey verify bodies:

* ``RequestBodyLimitMiddleware`` (``settings.app.MAX_BODY_SIZE``, 10 MiB) —
  bytes. Sits at position 5 of the request stack, outside the router, so it
  short-circuits with 413 and the body is never handed to the route.
* the passkey credential shape bound in ``auth.schemas`` — entries + nesting
  depth, for bodies that fit under the byte cap.

The property that matters is the ORDER: an oversized body must produce the
middleware's 413 envelope, not a 422 from pydantic. The proof here is
behavioural, not structural — the test counts how many bytes the inner app
actually read off the receive channel. Zero bytes read means pydantic never
parsed anything.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from src.app.core.config import settings
from src.app.core.errors import setup_exception_handlers
from src.app.middleware.body_limit import RequestBodyLimitMiddleware
from src.app.modules.auth.schemas import (
    PASSKEY_CREDENTIAL_MAX_DEPTH,
    PASSKEY_CREDENTIAL_MAX_ENTRIES,
    PasskeyLoginVerifyRequest,
    PasskeyRegistrationVerifyRequest,
)

MAX_BODY_SIZE = settings.app.MAX_BODY_SIZE


# --- a minimal app that declares the REAL request models --------------------


def _build_app(handler_calls: list[str]) -> FastAPI:
    app = FastAPI()
    setup_exception_handlers(app)

    @app.post("/auth/passkey/register/verify")
    async def register_verify(body: PasskeyRegistrationVerifyRequest) -> dict[str, Any]:
        handler_calls.append("register")
        return {"credential_len": len(body.credential)}

    @app.post("/auth/passkey/login/verify")
    async def login_verify(body: PasskeyLoginVerifyRequest) -> dict[str, Any]:
        handler_calls.append("login")
        return {"credential_len": len(body.credential)}

    return app


class Outcome:
    """What the middleware stack did with one request."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes, inner_bytes_read: int) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.inner_bytes_read = inner_bytes_read

    @property
    def json(self) -> Any:
        return json.loads(self.body)


async def _post_raw(path: str, raw: bytes, *, content_length: int | None = None) -> Outcome:
    """``_post`` for a body supplied as raw bytes.

    Deeply nested JSON cannot go through ``json.dumps`` — the encoder is
    recursive and raises on exactly the shapes these tests build.
    """
    return await _drive(path, [raw], content_length=content_length)


async def _post(path: str, payload: Any, *, content_length: int | None = None) -> Outcome:
    """Drive middleware+app over a real ASGI scope, counting bytes read.

    ``inner_bytes_read`` counts what the wrapped app pulled off ``receive``.
    The middleware only forwards what it has already counted, so a 413 with
    zero bytes read proves the router — and therefore pydantic — never saw the
    body.
    """
    return await _drive(path, [json.dumps(payload).encode()], content_length=content_length)


async def _drive(path: str, chunks: list[bytes], *, content_length: int | None = None) -> Outcome:
    """Drive middleware+app over a real ASGI scope, counting bytes read.

    ``inner_bytes_read`` counts what the wrapped app pulled off ``receive``.
    The middleware only forwards what it has already counted, so a 413 with
    zero bytes read proves the router — and therefore pydantic — never saw the
    body.
    """
    handler_calls: list[str] = []
    raw = b"".join(chunks)
    stack = RequestBodyLimitMiddleware(_build_app(handler_calls), max_body_size=MAX_BODY_SIZE)
    read_bytes = 0

    async def receive() -> Message:
        nonlocal read_bytes
        read_bytes += len(chunks[0]) if chunks else 0
        if chunks:
            chunk = chunks.pop(0)
        else:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    declared = len(raw) if content_length is None else content_length
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(declared).encode()),
        ],
        "client": ("127.0.0.1", 40000),
        "server": ("testserver", 80),
    }

    status_holder: dict[str, Any] = {}
    body_parts: list[bytes] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            status_holder["status"] = message["status"]
            status_holder["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await stack(scope, receive, send)
    assert handler_calls == [] or status_holder["status"] == 200
    return Outcome(
        status=status_holder["status"],
        headers=status_holder["headers"],
        body=b"".join(body_parts),
        inner_bytes_read=read_bytes,
    )


def _oversized(pad: int) -> dict[str, Any]:
    """A body over MAX_BODY_SIZE that is ALSO schema-invalid."""
    return {
        "credential": {"id": "x", "tooManyKeys": {f"k{i}": i for i in range(500)}, "pad": "y" * pad},
        "challenge_id": "c",
    }


def _oversized_and_deep(pad: int) -> dict[str, Any]:
    """Oversized *and* deeper than PASSKEY_CREDENTIAL_MAX_DEPTH."""
    deep: dict[str, Any] = {"leaf": "z"}
    for _ in range(64):
        deep = {"n": deep}
    return {"credential": {"id": "x", "response": deep, "pad": "y" * pad}, "challenge_id": "c"}


# --- the cap, confirmed ------------------------------------------------------


def test_max_body_size_in_force() -> None:
    assert MAX_BODY_SIZE == 10 * 1024 * 1024


# --- middleware wins, on both passkey routes --------------------------------


@pytest.mark.parametrize(
    "path",
    ["/auth/passkey/register/verify", "/auth/passkey/login/verify"],
)
async def test_oversized_body_rejected_413_without_being_parsed(path: str) -> None:
    out = await _post(path, _oversized(MAX_BODY_SIZE + 1))

    assert out.status == 413, f"expected the middleware's 413, got {out.status}: {out.body[:300]!r}"
    # The body was never read by the wrapped app: no schema parsing happened.
    assert out.inner_bytes_read == 0
    assert out.json["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert out.headers["x-max-body-size"] == str(MAX_BODY_SIZE)


async def test_oversized_and_deep_body_still_413_not_422() -> None:
    """Both bounds violated -> the byte bound is the one that answers."""
    out = await _post("/auth/passkey/login/verify", _oversized_and_deep(MAX_BODY_SIZE + 1))

    assert out.status == 413
    assert out.inner_bytes_read == 0
    assert out.json["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_under_the_byte_cap_but_over_the_entry_cap_is_422() -> None:
    """The shape bound covers what the byte bound cannot: a small wide dict."""
    out = await _post(
        "/auth/passkey/login/verify",
        {"credential": {f"k{i}": i for i in range(PASSKEY_CREDENTIAL_MAX_ENTRIES + 1)}, "challenge_id": "c"},
    )

    assert out.status == 422
    assert out.inner_bytes_read > 0  # the body WAS parsed — that is where it failed
    loc = out.json["detail"][0]["loc"]
    assert loc == ["body", "credential"]


async def test_under_the_byte_cap_but_deeply_nested_is_400() -> None:
    """Same for depth: a ~7 KiB body can still nest 1000 deep.

    The body is built as raw bytes because ``json.dumps`` on a 1000-deep
    structure is itself recursive and blows the stack — which is exactly why
    the server-side rejection has to be iterative.
    """
    out = await _post_raw(
        "/auth/passkey/login/verify",
        b'{"credential":' + (b'{"n":' * 1000 + b'1' + b'}' * 1000) + b',"challenge_id":"c"}',
    )

    assert out.status == 400
    assert out.inner_bytes_read > 0  # the body WAS read; the bound lives in the schema
    assert out.json["error"]["code"] == "VALIDATION_ERROR"
    assert f"at most {PASSKEY_CREDENTIAL_MAX_DEPTH} levels deep, got 1000" in out.json["detail"]


async def test_deeply_nested_body_is_never_a_500() -> None:
    """The rejection stays 4xx at every depth and never echoes the payload.

    A pydantic ValidationError here would make FastAPI's handler json-encode the
    deep ``input`` it attaches, which recurses out of the stack and 500s
    (measured: RecursionError from nesting ~998, for any model, passkey or
    not). The bound raises the repo's domain error instead, so the response
    stays small and the status stays 4xx at 20k levels.
    """
    for depth in (7, 100, 900, 1000, 20_000):
        out = await _post_raw(
            "/auth/passkey/login/verify",
            b'{"credential":' + (b'{"n":' * depth + b'1' + b'}' * depth) + b',"challenge_id":"c"}',
        )
        assert out.status == 400, f"depth={depth} came back {out.status}, not a 4xx"
        assert len(out.body) < 300, f"depth={depth} echoed the payload ({len(out.body)} bytes)"


# --- a large but legitimate credential must still go through -----------------


async def test_valid_large_credential_accepted() -> None:
    """The bounds must not cost real clients anything.

    A real attestation carries a multi-KB ``attestationObject``; this payload
    sits at both caps (32 entries, depth 6) and is ~1 MiB of JSON — under the
    byte cap, inside both shape caps, and it must reach the handler.
    """
    nested: dict[str, Any] = {"leaf": "signature-blob"}
    for _ in range(PASSKEY_CREDENTIAL_MAX_DEPTH - 2):
        nested = {"n": nested}
    credential: dict[str, Any] = {
        "id": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "rawId": "SZYN5YgOjGh0NBcPZHZgW4_krrmihjLHmVzzuoMdl2M",
        "type": "public-key",
        "attestationObject": "o2NmbXRkbm9uZWdhdHRTdG10" * 20_000,
        "clientExtensionResults": {},
        "response": nested,
    }
    credential.update({f"ext{i}": "v" for i in range(PASSKEY_CREDENTIAL_MAX_ENTRIES - len(credential))})
    assert len(credential) == PASSKEY_CREDENTIAL_MAX_ENTRIES

    out = await _post(
        "/auth/passkey/login/verify",
        {"credential": credential, "challenge_id": "c"},
    )

    assert out.status == 200, out.body[:300]
    assert out.json == {"credential_len": PASSKEY_CREDENTIAL_MAX_ENTRIES}
    assert out.inner_bytes_read > 0


# --- the streaming (chunked) path, stated honestly ---------------------------


async def test_chunked_oversized_body_is_rejected_even_without_content_length() -> None:
    """A client that omits Content-Length is still refused — but with 400, not 413.

    Measured on this stack (pydantic 2.11 / fastapi 0.115): the streaming path
    cannot return the middleware's 413. The middleware only notices the overrun
    once the router has begun reading the body, so ``limited_receive`` raises
    ``BodySizeLimitExceeded`` INSIDE Starlette's own body reader; Starlette
    catches it and answers 400 "There was an error parsing the body" before the
    middleware's ``except`` clause can call ``_send_413``. Reproduced with a
    trivial ``{value: str}`` model that carries NO passkey bounds at all, so it
    is pre-existing and orthogonal to the shape bound added here — reported to
    Main (owner of ``src/app/middleware/body_limit.py``), not fixed in scope.

    What DOES matter for this ticket: an oversized body is never accepted and
    never reaches schema validation as a success. Assert that invariant, plus
    the honest status.
    """
    handler_calls: list[str] = []
    raw = json.dumps(_oversized(MAX_BODY_SIZE + 1)).encode()
    stack = RequestBodyLimitMiddleware(_build_app(handler_calls), max_body_size=MAX_BODY_SIZE)

    chunks = [raw[i : i + 64 * 1024] for i in range(0, len(raw), 64 * 1024)]

    async def receive() -> Message:
        if chunks:
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/auth/passkey/login/verify",
        "raw_path": b"/auth/passkey/login/verify",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 40000),
        "server": ("testserver", 80),
    }
    status_holder: dict[str, Any] = {}
    body_parts: list[bytes] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            status_holder["status"] = message["status"]
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await stack(scope, receive, send)

    # Rejected outright — the route handler never ran to completion.
    assert 400 <= status_holder["status"] < 500, f"expected a 4xx refusal, got {status_holder['status']}"
    assert status_holder["status"] != 200
    assert handler_calls == []
    # Not parsed into a credential and stored: the response is an error envelope,
    # not the {"credential_len": ...} success shape.
    payload = json.loads(b"".join(body_parts))
    assert "credential_len" not in payload
