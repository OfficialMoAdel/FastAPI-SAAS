"""GZip compression OFF the event loop (F-4).

WHY
    Starlette's ``GZipMiddleware`` compresses on the event loop: its
    ``GZipResponder.send_with_gzip`` calls ``gzip.GzipFile.write()`` inline,
    so a large response blocks every concurrent request for the duration of
    the deflate. Measured at ``compresslevel=4``: 1.315 ms for 256 KB and
    5.508 ms for 1 MB -- pure event-loop stall, on the response path.

    Unlike ``re`` (which holds the GIL across a match), **zlib releases the
    GIL** (2-thread ratio 0.946 vs 1.888 for ``re``), so moving the
    compression to a worker thread is a genuine event-loop win rather than a
    relocation of the stall. This is one of the few places in the codebase
    where a thread offload provably works.

WHAT
    ``ThreadedGZipResponder`` is Starlette 0.40.0's ``GZipResponder`` with
    the three ``gzip_file.write``/``close`` sites moved into an executor; the
    response framing (the Content-Length / Content-Encoding / Vary rewriting,
    the ``minimum_size`` passthrough, the streaming branch and the
    ``content-encoding``-already-set passthrough) is otherwise byte-for-byte
    the upstream logic, so the emitted bytes are identical. The framework
    conformance -- that our copy still matches the installed responder -- is
    asserted by ``tests/unit/middleware/test_gzip_threaded.py``, and a
    byte-equality differential proves the output.

    The responder is a SUBCLASS, so ``isinstance`` checks against the
    upstream ``GZipResponder`` still hold and the ``with``-block close is
    inherited.

RUNNER
    Compression runs on a DEDICATED small pool, not the default executor:
    JWT signing/decoding already uses the default executor
    (``jwt_service._jwt_encode``), and letting a burst of large responses
    contend with token work would trade one stall for another. Awaiting a
    saturated pool queues coroutines; it does not block the loop.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware, GZipResponder
from starlette.types import Message, Receive, Scope, Send

#: Bounded, and separate from the default executor (see RUNNER above).
_GZIP_EXECUTOR: Final[ThreadPoolExecutor] = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="gzip",
)


class ThreadedGZipResponder(GZipResponder):
    """``GZipResponder`` with the deflate work moved into an executor."""

    def _deflate_all(self, body: bytes) -> None:
        """Whole-response compress + finish (the ``not more_body`` branch)."""
        self.gzip_file.write(body)
        self.gzip_file.close()

    def _deflate_chunk(self, body: bytes, final: bool) -> None:
        """One chunk of a streaming response, finishing when ``final``."""
        self.gzip_file.write(body)
        if final:
            self.gzip_file.close()

    async def send_with_gzip(self, message: Message) -> None:
        message_type = message["type"]
        if message_type == "http.response.start":
            # Don't send the initial message until we've determined how to
            # modify the outgoing headers correctly.
            self.initial_message = message
            headers = Headers(raw=self.initial_message["headers"])
            self.content_encoding_set = "content-encoding" in headers
        elif message_type == "http.response.body" and self.content_encoding_set:
            if not self.started:
                self.started = True
                await self.send(self.initial_message)
            await self.send(message)
        elif message_type == "http.response.body" and not self.started:
            self.started = True
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            if len(body) < self.minimum_size and not more_body:
                # Don't apply GZip to small outgoing responses.
                await self.send(self.initial_message)
                await self.send(message)
            elif not more_body:
                # Standard GZip response.
                await self._run(self._deflate_all, body)
                body = self.gzip_buffer.getvalue()

                headers = MutableHeaders(raw=self.initial_message["headers"])
                headers["Content-Encoding"] = "gzip"
                headers["Content-Length"] = str(len(body))
                headers.add_vary_header("Accept-Encoding")
                message["body"] = body

                await self.send(self.initial_message)
                await self.send(message)
            else:
                # Initial body in streaming GZip response.
                headers = MutableHeaders(raw=self.initial_message["headers"])
                headers["Content-Encoding"] = "gzip"
                headers.add_vary_header("Accept-Encoding")
                del headers["Content-Length"]

                await self._run(self.gzip_file.write, body)
                message["body"] = self.gzip_buffer.getvalue()
                self.gzip_buffer.seek(0)
                self.gzip_buffer.truncate()

                await self.send(self.initial_message)
                await self.send(message)
        elif message_type == "http.response.body":
            # Remaining body in streaming GZip response.
            body = message.get("body", b"")
            more_body = message.get("more_body", False)

            await self._run(self._deflate_chunk, body, not more_body)

            message["body"] = self.gzip_buffer.getvalue()
            self.gzip_buffer.seek(0)
            self.gzip_buffer.truncate()

            await self.send(message)

    @staticmethod
    async def _run(func: object, *args: object) -> None:
        """Run one compression step on the dedicated pool.

        ``func``/``args`` are the sync ``gzip``/``GzipFile`` callables; the
        step is awaited before the buffer is read, so the responder's buffer
        is never touched from two threads at once.
        """
        await asyncio.get_running_loop().run_in_executor(_GZIP_EXECUTOR, func, *args)  # type: ignore[arg-type]


class ThreadedGZipMiddleware(GZipMiddleware):
    """``GZipMiddleware`` that uses ``ThreadedGZipResponder``.

    ``GZipMiddleware.__call__`` hard-codes the upstream responder class, so
    the eight-line dispatch is mirrored here; everything else is inherited.
    ``responder_class`` is an attribute (not a literal) so a test can observe
    WHICH thread the deflate runs on.
    """

    responder_class: type[ThreadedGZipResponder] = ThreadedGZipResponder

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "gzip" in headers.get("Accept-Encoding", ""):
                responder = self.responder_class(self.app, self.minimum_size, compresslevel=self.compresslevel)
                await responder(scope, receive, send)
                return
        await self.app(scope, receive, send)
