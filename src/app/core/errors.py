"""Standardized error responses and global exception handling.

Provides consistent error response format across all endpoints:
{
    "error": {
        "code": "ERROR_CODE",
        "message": "Human-readable message",
        "details": {...},  # Optional additional context
        "request_id": "uuid"  # For debugging
    }
}

Handlers are registered by :func:`setup_exception_handlers` from three
sources: the framework handlers (Starlette/pydantic/SQLAlchemy/cache), ONE
generic handler for the whole ``AppException`` tree, and the global catch-all.
There is no domain mapping table — every ``AppException`` subclass declares its
own HTTP presentation (``http_status`` / ``response_code`` / ``public_message``
/ ``log_message`` / ``log_error`` / ``http_details()`` / ``http_headers()``)
beside itself, in ``src/app/shared/exceptions.py`` or the owning module.
"""

from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal, NamedTuple

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog import get_logger

from src.app.core.exceptions.cache_exceptions import CacheException
from src.app.shared.error_codes import (
    ErrorCode,  # re-exported; canonical home is the leaf
    status_to_error_code,
)
from src.app.shared.exceptions import AppException

logger = get_logger(__name__)


def middleware_error_response(
    code: str,
    message: str,
    status_code: int = 400,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an envelope response for native ASGI middleware (item 7).

    Middleware runs before routing, so it cannot raise domain exceptions
    into the table — but it must not hand-roll envelopes either. This is
    the ONE failure interface middleware calls: same shape as
    :func:`create_error_response`, minus request-id (unavailable pre-route).
    """
    return create_error_response(code=code, message=message, status_code=status_code, headers=headers)


RejectKind = Literal[
    "banned_ip",
    "admission_unavailable",
    "internal_error",
    "auto_banned",
    "rate_limited",
    "tenant_header_missing",
    "tenant_access_denied",
    "tenant_inactive",
    "payload_too_large",
    "invalid_input",
    "attack_blocked",
    "attack_banned",
    "bad_bot",
    "blacklisted_ip",
]


class RejectRow(NamedTuple):
    """One owned middleware rejection row: code + status + message template.

    ``message`` is a ``str.format`` template resolved from ``middleware_reject``
    ``**params``; ``headers_builder`` derives headers from the same params
    (primitives only — no guard-layer imports, no layering violations).
    """

    code: str
    status: int
    message: str
    headers_builder: Callable[[Mapping[str, Any]], dict[str, str]] | None = None


def _banned_ip_headers(params: Mapping[str, Any]) -> dict[str, str]:
    """X-Banned / X-Ban-Reason + conditional Retry-After from ban primitives."""
    import time

    headers = {"X-Banned": "true", "X-Ban-Reason": str(params.get("ban_reason") or "Auto-ban")}
    expires_at = params.get("ban_expires_at")
    if expires_at:
        retry_after = int(float(expires_at) - time.time()) + 1
        if retry_after > 0:
            headers["Retry-After"] = str(retry_after)
    return headers


def _max_size_headers(params: Mapping[str, Any]) -> dict[str, str]:
    return {"x-max-body-size": str(params["max_size"])}


# Owned rejection rows (card 6): every guard rejection resolves from here so
# code/message/status/headers cannot drift between call sites. Strings are
# verbatim what the guards previously hand-built.
REJECT_CATALOG: dict[str, RejectRow] = {
    "banned_ip": RejectRow(
        ErrorCode.FORBIDDEN, 403, "Forbidden: IP address banned due to abuse", _banned_ip_headers
    ),
    "admission_unavailable": RejectRow(
        ErrorCode.SERVICE_UNAVAILABLE, 503, "Request admission temporarily unavailable"
    ),
    "internal_error": RejectRow(ErrorCode.INTERNAL_ERROR, 500, "Internal server error"),
    "auto_banned": RejectRow(
        ErrorCode.FORBIDDEN, 403, "Forbidden: IP banned after {violation_count} rate limit violations"
    ),
    "rate_limited": RejectRow(ErrorCode.RATE_LIMITED, 429, "Rate limit exceeded"),
    "tenant_header_missing": RejectRow(
        ErrorCode.VALIDATION_ERROR, 400, "Missing required header: {header}"
    ),
    "tenant_access_denied": RejectRow(ErrorCode.FORBIDDEN, 403, "Access denied"),
    "tenant_inactive": RejectRow(ErrorCode.FORBIDDEN, 403, "Tenant inactive. Contact support."),
    "payload_too_large": RejectRow(
        ErrorCode.PAYLOAD_TOO_LARGE, 413, "Request body too large", _max_size_headers
    ),
    "invalid_input": RejectRow(ErrorCode.INVALID_INPUT, 400, "{message}"),
    "attack_blocked": RejectRow(
        ErrorCode.VALIDATION_ERROR, 400, "Bad Request - Attack pattern detected"
    ),
    "attack_banned": RejectRow(ErrorCode.FORBIDDEN, 403, "Forbidden - Attack detected and IP banned"),
    "bad_bot": RejectRow(ErrorCode.FORBIDDEN, 403, "Forbidden - Bad bot detected"),
    "blacklisted_ip": RejectRow(ErrorCode.FORBIDDEN, 403, "Forbidden - IP is blacklisted"),
}


def middleware_reject(
    kind: RejectKind,
    *,
    headers: Mapping[str, str] | None = None,
    **params: Any,
) -> JSONResponse:
    """Resolve a rejection row from the catalog and build its envelope.

    Guards call this with a kind + primitives instead of hand-building
    code/message/status/headers at each call site. Unknown kinds fail loud
    (``ValueError``) — a typo must never become a shapeless 500.
    ``headers`` merges over table-built headers (for verdict passthrough).
    """
    try:
        row = REJECT_CATALOG[kind]
    except KeyError:
        raise ValueError(f"Unknown middleware reject kind: {kind!r}") from None
    message = row.message.format(**params)
    built = dict(row.headers_builder(params)) if row.headers_builder is not None else {}
    if headers is not None:
        built.update(headers)
    return middleware_error_response(code=row.code, message=message, status_code=row.status, headers=built or None)


def _json_safe(value: Any) -> Any:
    """Coerce values Starlette cannot serialize into JSON-safe ones.

    ``details`` payloads are assembled from domain exceptions and routinely
    carry rich types (``UUID``, ``datetime``, ``Decimal``). Starlette's
    ``JSONResponse`` calls ``json.dumps`` with no ``default`` hook, so a stray
    UUID raised ``TypeError: Object of type UUID is not JSON serializable``
    and turned a clean 4xx (e.g. session-limit 429) into a 500.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(v) for v in value]
    return str(value)


def create_error_response(
    code: str,
    message: str,
    status_code: int = 400,
    details: dict | None = None,
    request_id: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Create a standardized error response.

    Args:
        code: Error code from ErrorCode class
        message: Human-readable error message
        status_code: HTTP status code
        details: Optional additional context
        request_id: Request ID for tracing
        headers: Optional HTTP headers for response

    Returns
    -------
        JSONResponse with standardized error format

    Note:
        Includes 'detail' key for backward compatibility with FastAPI's default format
    """
    error_inner: dict[str, Any] = {
        "code": code,
        "message": message,
    }

    if details:
        error_inner["details"] = _json_safe(details)

    if request_id:
        error_inner["request_id"] = request_id

    error_body: dict[str, Any] = {
        "error": error_inner,
        # Backward compatibility with FastAPI's default HTTPException format
        "detail": message,
    }

    return JSONResponse(status_code=status_code, content=error_body, headers=headers)


def _register_framework_handlers(app: FastAPI) -> None:
    """Framework-level handlers: HTTP, cache, Pydantic validation, DB timeout."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Handle HTTP exceptions with standardized format."""
        request_id = getattr(request.state, "request_id", None)

        code = status_to_error_code(exc.status_code)

        logger.warning(
            "HTTP exception",
            status_code=exc.status_code,
            detail=exc.detail,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=code,
            message=str(exc.detail) if exc.detail else "An error occurred",
            status_code=exc.status_code,
            request_id=request_id,
            # P1-9 FIX: preserve exc.headers — 401s must carry
            # WWW-Authenticate: Bearer (RFC 6750), and SSO error paths rely on
            # Set-Cookie headers to clear poisoned session cookies.
            headers=exc.headers,
        )

    @app.exception_handler(CacheException)
    async def cache_exception_handler(request: Request, exc: CacheException) -> JSONResponse:
        """Handle custom cache exceptions."""
        request_id = getattr(request.state, "request_id", None)
        logger.error("Cache error", error=str(exc), exc_info=True, request_id=request_id)
        return create_error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="Internal Server Error",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"error_type": "cache_error"},
            request_id=request_id,
        )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
        """Handle Pydantic validation errors."""
        request_id = getattr(request.state, "request_id", None)

        # Format validation errors
        details: dict[str, Any] = {"fields": []}
        for error in exc.errors():
            details["fields"].append(
                {
                    "location": ".".join(str(loc) for loc in error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                }
            )

        logger.warning(
            "Validation error",
            error_count=len(exc.errors()),
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed",
            status_code=422,
            details=details,
            request_id=request_id,
        )

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def db_timeout_exception_handler(request: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        """Handle database pool timeout errors.

        Returns HTTP 503 Service Unavailable when the DB pool is exhausted.
        """
        request_id = getattr(request.state, "request_id", None)

        logger.error(
            "Database connection pool exhausted",
            error=str(exc),
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Database connection pool exhausted. Please try again later.",
            status_code=503,
            request_id=request_id,
        )


def _make_app_exception_handler() -> Callable[[Request, Any], Coroutine[Any, Any, JSONResponse]]:
    """The ONE handler for the whole ``AppException`` tree.

    Reads the presentation declared beside each exception
    (``http_status`` / ``response_code`` / ``public_message`` /
    ``log_message`` / ``log_error`` / ``http_details()`` / ``http_headers()``)
    rather than consulting a mapping here.

    A handler registered for a *more specific* class still beats this one:
    Starlette resolves by walking ``type(exc).__mro__`` and returning the first
    registered class. That is how ``CacheException`` keeps its deliberate 500
    (see ``_register_framework_handlers``), and this fallback beats the global
    500 catch-all for every other domain error.
    """

    async def _handler(request: Request, exc: AppException) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log = logger.error if exc.log_error else logger.warning
        log(exc.resolve_log_message(), error=str(exc), request_id=request_id, path=request.url.path)
        # Derived headers first, then the raiser's own dict wins, so a class
        # hook (RateLimitExceededError's Retry-After) and an explicit override
        # can coexist without either silently clobbering the other.
        headers = exc.http_headers()
        if exc.headers:
            headers = {**(headers or {}), **exc.headers}
        return create_error_response(
            code=exc.resolve_response_code(),
            message=exc.resolve_public_message(),
            status_code=exc.resolve_http_status(),
            details=exc.http_details(),
            request_id=request_id,
            headers=headers or None,
        )

    return _handler


def _register_app_exception_fallback(app: FastAPI) -> None:
    """Register the one generic handler for the ``AppException`` tree.

    Registered *after* the table rows; order does not decide the winner
    (Starlette walks the MRO and takes the most specific registered class),
    but keeping it last documents the intent: table rows are the overrides,
    this is the default.
    """
    app.exception_handler(AppException)(_make_app_exception_handler())  # type: ignore


def _register_global_handler(app: FastAPI) -> None:
    """Catch-all handler for unexpected exceptions."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all handler for unexpected exceptions."""
        request_id = getattr(request.state, "request_id", None)

        # Log full exception for debugging
        logger.exception(
            "Unhandled exception",
            error=str(exc),
            error_type=type(exc).__name__,
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        return create_error_response(
            code=ErrorCode.INTERNAL_ERROR,
            message="An internal error occurred",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app.

    Thin composer over the framework handlers, the generic
    ``AppException`` fallback, and the global catch-all. There is no longer a
    domain table: every ``AppException`` subclass declares its HTTP
    presentation beside itself (candidate 3), so the fallback is the single
    domain handler and only framework exceptions get bespoke ones. Call this
    after creating the app:
        app = FastAPI()
        setup_exception_handlers(app)
    """
    _register_framework_handlers(app)
    _register_app_exception_fallback(app)
    _register_global_handler(app)


# `_status_to_error_code` moved to src/app/shared/error_codes.py as
# `status_to_error_code` (candidate 3, pass 3): the auth exceptions derive
# their own `response_code` from their status, and the exception leaf may not
# import core. The framework HTTP handler above imports the leaf function.
