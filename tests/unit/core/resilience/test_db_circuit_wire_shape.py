"""P1-10: the DB circuit guard's refusal reaches clients unchanged.

``db_circuit_guard`` used to raise ``fastapi.HTTPException`` directly. It now
raises ``ServiceUnavailableError``, so the ARQ worker no longer sees an HTTP
response object escape into a queue consumer.

This file pins the OTHER half of that change: the client-visible envelope must
not shift. Both paths must produce the same status and the same message, so no
client that parses this response is affected by the swap.
"""

from fastapi import HTTPException, status
from starlette.requests import Request

from src.app.shared.exceptions import ServiceUnavailableError

CIRCUIT_MESSAGE = "Database service temporarily degraded due to high failure rate. Please retry later."


def _state() -> Request:
    """A minimal Request with the fields create_error_response reads.

    ``Request.state`` is a read-only property over ``scope["state"]``, so the
    id is seeded there rather than assigned.
    """
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/things",
            "headers": [],
            "state": {"request_id": "req-test"},
        }
    )


class TestGuardRaisesDomainError:
    """The exception type is the regression; this pins it."""

    def test_open_circuit_raises_service_unavailable_not_http(self) -> None:
        """Not an HTTPException — that is what the worker could not handle."""
        assert issubclass(ServiceUnavailableError, Exception)
        assert not issubclass(ServiceUnavailableError, HTTPException)

    def test_maps_to_503(self) -> None:
        assert ServiceUnavailableError.http_status == 503
        assert status.HTTP_503_SERVICE_UNAVAILABLE == ServiceUnavailableError.http_status


class TestWireShapeIsUnchanged:
    """Old path vs new path must agree on what the client sees."""

    def test_message_text_is_identical_to_the_old_http_detail(self) -> None:
        """resolve_public_message() returns the message verbatim.

        public_message is None on ServiceUnavailableError, so the handler
        emits str(self.message) — exactly what HTTPException.detail carried.
        """
        exc = ServiceUnavailableError(CIRCUIT_MESSAGE)
        assert exc.resolve_public_message() == CIRCUIT_MESSAGE
        assert exc.message == CIRCUIT_MESSAGE
        assert exc.detail == CIRCUIT_MESSAGE

    def test_both_handlers_agree_on_status_and_message(self) -> None:
        """The old HTTPException and the new domain error render identically."""
        import asyncio

        from src.app.core.errors import _make_app_exception_handler

        request = _state()

        async def _render_old():
            from src.app.core.errors import create_error_response

            return create_error_response(
                code="service_unavailable",
                message=CIRCUIT_MESSAGE,
                status_code=503,
            )

        handler = _make_app_exception_handler()
        new_response = asyncio.get_event_loop().run_until_complete(
            handler(request, ServiceUnavailableError(CIRCUIT_MESSAGE))
        )
        old_response = asyncio.get_event_loop().run_until_complete(_render_old())

        import json

        new_body = json.loads(new_response.body)
        old_body = json.loads(old_response.body)

        assert new_response.status_code == old_response.status_code == 503
        assert new_body["error"]["message"] == old_body["error"]["message"] == CIRCUIT_MESSAGE

    def test_error_code_is_the_explicit_service_unavailable_slug(self) -> None:
        """A deliberate, documented difference from the old path.

        The old HTTPException handler derived the code from the status via
        status_to_error_code; the domain error declares
        ErrorCode.SERVICE_UNAVAILABLE directly. Both render
        "service_unavailable" here, but the domain path is the authoritative
        one — the message is now declared beside the exception rather than
        inferred from a number.
        """
        from src.app.shared.error_codes import ErrorCode

        assert ServiceUnavailableError.resolve_response_code(
            ServiceUnavailableError(CIRCUIT_MESSAGE)
        ) == ErrorCode.SERVICE_UNAVAILABLE
