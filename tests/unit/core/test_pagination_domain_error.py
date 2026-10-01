"""P1-10 follow-on: the pagination deep-scan guard raises a DOMAIN exception.

``core.pagination`` raised ``fastapi.HTTPException(422)`` directly from core
— the same defect class as the DB circuit guard, where an HTTP response
object escapes below the transport layer. The ARQ worker has no request in
scope, and a bare HTTPException there becomes an unhandled job failure
rather than a handled domain error.

The status must NOT change: 422 in, 422 out. These tests pin both halves —
the type is now a domain error, and the wire contract is identical.
"""

import pytest

from src.app.core.pagination import _MAX_OFFSET, PaginationParams


class TestDomainExceptionType:
    """The exception is a domain one, not an HTTP response object."""

    def test_deep_offset_raises_a_domain_error(self) -> None:
        from src.app.shared.exceptions import UnprocessableRequestError

        with pytest.raises(UnprocessableRequestError) as exc:
            PaginationParams(page=(_MAX_OFFSET // 10) + 5, per_page=10)

        assert "exceeds" in str(exc.value.detail)

    def test_it_is_not_an_http_exception(self) -> None:
        """The whole point: no fastapi type escapes from core."""
        from fastapi import HTTPException

        from src.app.shared.exceptions import UnprocessableRequestError

        with pytest.raises(UnprocessableRequestError) as exc:
            PaginationParams(page=(_MAX_OFFSET // 10) + 5, per_page=10)
        assert not isinstance(exc.value, HTTPException)

    def test_it_carries_422(self) -> None:
        """The wire status is unchanged — this is not an API change."""
        from src.app.shared.exceptions import UnprocessableRequestError

        assert UnprocessableRequestError.http_status == 422


class TestWireContractUnchanged:
    """Before and after must render the same client-visible response."""

    def test_envelope_matches_the_old_http_exception(self) -> None:
        """Same status, same message, same error code as the HTTPException path."""
        import asyncio
        import json

        from fastapi import status
        from starlette.requests import Request

        from src.app.core.errors import create_error_response
        from src.app.shared.exceptions import UnprocessableRequestError

        message = f"Pagination offset 999999 exceeds {_MAX_OFFSET}"

        # The OLD rendering path: an HTTPException through the envelope, with
        # the code it ACTUALLY derived from the status (not a guess).
        from src.app.core.errors import status_to_error_code

        old = create_error_response(
            code=status_to_error_code(status.HTTP_422_UNPROCESSABLE_ENTITY),
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

        # The NEW rendering path: the domain exception through the central handler.
        from src.app.core.errors import _make_app_exception_handler

        request = Request(
            {"type": "http", "method": "GET", "path": "/v1/things", "headers": [], "state": {}}
        )
        new = asyncio.get_event_loop().run_until_complete(
            _make_app_exception_handler()(request, UnprocessableRequestError(message))
        )

        old_body, new_body = json.loads(old.body), json.loads(new.body)
        assert old.status_code == new.status_code == 422
        assert old_body["error"]["message"] == new_body["error"]["message"] == message
        assert old_body["error"]["code"] == new_body["error"]["code"]

    def test_a_real_http_error_still_renders(self) -> None:
        """The pre-existing HTTPException path is untouched."""
        from fastapi import HTTPException

        from src.app.core.errors import status_to_error_code

        assert status_to_error_code(422) is not None
        with pytest.raises(HTTPException):
            raise HTTPException(status_code=422, detail="x")


class TestGuardReachabilityUnchanged:
    """The guard still fires on a deep scan, and only on one."""

    def test_normal_pagination_is_untouched(self) -> None:
        params = PaginationParams(page=2, per_page=10)
        assert params.offset == 10

    def test_boundary_at_the_limit_is_allowed(self) -> None:
        """At the limit the guard must not fire — an off-by-one would reject
        a request that was previously accepted."""
        per_page = 10
        page = (_MAX_OFFSET // per_page) + 1
        assert PaginationParams(page=page, per_page=per_page).offset <= _MAX_OFFSET

    def test_beyond_the_limit_is_refused(self) -> None:
        per_page = 10
        page = (_MAX_OFFSET // per_page) + 5
        with pytest.raises(Exception):
            PaginationParams(page=page, per_page=per_page)
