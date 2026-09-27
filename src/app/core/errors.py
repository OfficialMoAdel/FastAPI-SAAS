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
from types import MappingProxyType
from typing import Any, Final, Literal, NamedTuple, TypedDict, Unpack, overload

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
from src.app.shared.exceptions import (
    AppException,
    ForbiddenError,
    RateLimitExceededError,
    ServiceUnavailableError,
)
from src.app.shared.exceptions import (
    ValidationError as DomainValidationError,
)

logger = get_logger(__name__)


def middleware_error_response(
    code: str,
    message: str,
    status_code: int = 400,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an envelope response for native ASGI middleware (item 7).

    Middleware runs before routing, so it cannot raise a domain exception
    into the app's handler — but it must not hand-roll envelopes either.
    This is the ONE failure interface middleware calls: same shape as
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
    """One owned middleware rejection row: WHICH EXCEPTION presents it.

    A row no longer declares ``error.code`` / HTTP status as literals. It
    names the ``AppException`` family whose *own* declaration already covers
    this outcome (``error``), and :meth:`resolve` reads the status and the
    envelope code back off that family through the very same
    ``resolve_http_status()`` / ``resolve_response_code()`` methods the one
    generic handler in this module calls. So ``rate_limited`` cannot disagree
    with ``RateLimitExceededError`` about 429, and ``tenant_access_denied``
    cannot disagree with ``ForbiddenError`` about 403 — a status change lands
    in one place, not two.

    Two outcomes have NO exception family to name — 413 ``PAYLOAD_TOO_LARGE``
    and 400 ``INVALID_INPUT`` — so those rows declare ``status`` and take the
    envelope code from :func:`status_to_error_code`, the canonical status map.
    A row that declares neither is a bug and fails loud in :meth:`resolve`.

    ``message`` is a ``str.format`` template resolved from the typed
    ``middleware_reject`` kwargs; ``headers_builder`` derives headers from the
    same primitives (primitives only — no guard-layer imports, no layering
    violations).
    """

    message: str
    error: type[AppException] | None = None
    status: int | None = None
    headers_builder: Callable[[Mapping[str, Any]], dict[str, str]] | None = None

    def resolve(self) -> tuple[str, int]:
        """The envelope ``(code, status)`` for this row, from its one source.

        Reads the named exception family through the public resolve hooks
        rather than reading ``http_status`` / ``response_code`` off the class
        directly, so a family that derives either value per instance still
        presents correctly here.
        """
        if self.error is not None:
            exc = self.error("")
            return exc.resolve_response_code(), exc.resolve_http_status()
        if self.status is not None:
            return status_to_error_code(self.status), self.status
        raise ValueError(f"Reject row {self.message!r} declares neither an error family nor a status")


# --- typed rejection params -------------------------------------------------
# One TypedDict per row that takes params, so a caller cannot pass a param the
# row does not template (``str.format`` would have raised KeyError at runtime,
# and a typo'd kwarg was silently accepted).


class _BannedIpParams(TypedDict, total=False):
    ban_reason: str | None
    ban_expires_at: float | None


class _AutoBannedParams(TypedDict):
    violation_count: int


class _TenantHeaderMissingParams(TypedDict):
    header: str


class _MaxSizeParams(TypedDict):
    max_size: int


class _MessageParams(TypedDict):
    message: str


_ParamlessKind = Literal[
    "admission_unavailable",
    "internal_error",
    "rate_limited",
    "tenant_access_denied",
    "tenant_inactive",
    "attack_blocked",
    "attack_banned",
    "bad_bot",
    "blacklisted_ip",
]


# Wire constants for X-Ban-Reason. The stored ban reason is free text an
# admin typed (up to 500 chars, modules/security/schemas.py) and must never
# be echoed to the banned client; the human-readable copy belongs on the
# SecurityEvent, which is where rate_limit.py already puts it.
BAN_REASON_AUTO_BAN = "auto_ban"
BAN_REASON_MANUAL_BAN = "manual_ban"

# Both auto-ban producers mint a reason with this prefix:
#   core/security/auto_ban.py    "Auto-ban: <n> rate limit violations ..."
#   middleware/attack_detection  "Auto-banned for <type> attack"
_AUTO_BAN_REASON_PREFIX = "Auto-ban"


def _classify_ban_reason(reason: object) -> str:
    """Reduce a stored ban reason to one of two wire constants.

    Classification is by PRODUCER, not by content. Note this cannot be
    "reason is present" — every producer supplies one: auto-ban defaults to
    ``reason="Auto-ban: rate limit violations exceeded"`` and manual ban
    defaults to ``"Manual ban by admin <id>"``. Only the producer prefix
    separates them.

    Unattributable reasons fall back to ``manual_ban``: an auto-ban always
    arrives prefixed, so the fallback is the direction that cannot
    mislabel an automated ban.
    """
    if isinstance(reason, str) and reason.startswith(_AUTO_BAN_REASON_PREFIX):
        return BAN_REASON_AUTO_BAN
    return BAN_REASON_MANUAL_BAN


def _banned_ip_headers(params: Mapping[str, Any]) -> dict[str, str]:
    """X-Banned / X-Ban-Reason + conditional Retry-After from ban primitives."""
    import time

    headers = {
        "X-Banned": "true",
        "X-Ban-Reason": _classify_ban_reason(params.get("ban_reason")),
    }
    expires_at = params.get("ban_expires_at")
    if expires_at:
        retry_after = int(float(expires_at) - time.time()) + 1
        if retry_after > 0:
            headers["Retry-After"] = str(retry_after)
    return headers


def _max_size_headers(params: Mapping[str, Any]) -> dict[str, str]:
    return {"x-max-body-size": str(params["max_size"])}


# Owned rejection rows (card 6; card 13 expresses them through the exception
# families). Every guard rejection resolves from here so code/message/status/
# headers cannot drift between call sites. Strings are verbatim what the guards
# previously hand-built; the (code, status) pair is no longer written here at
# all — it comes from the named family, or from `status_to_error_code` for the
# two outcomes no family covers.
#
# Read-only: this table resolves every guard rejection, so a stray assignment
# would silently repoint a status/code/header family process-wide. Frozen the
# same way as its two siblings — principal.DENIAL_TABLE (MappingProxyType) and
# rbac.PermissionGraph (frozen dataclass).
REJECT_CATALOG: Final[Mapping[RejectKind, RejectRow]] = MappingProxyType(
    {
        "banned_ip": RejectRow(
            "Forbidden: IP address banned due to abuse",
            error=ForbiddenError,
            headers_builder=_banned_ip_headers,
        ),
        "admission_unavailable": RejectRow(
            "Request admission temporarily unavailable", error=ServiceUnavailableError
        ),
        "internal_error": RejectRow("Internal server error", error=AppException),
        "auto_banned": RejectRow(
            "Forbidden: IP banned after {violation_count} rate limit violations", error=ForbiddenError
        ),
        "rate_limited": RejectRow("Rate limit exceeded", error=RateLimitExceededError),
        "tenant_header_missing": RejectRow("Missing required header: {header}", error=DomainValidationError),
        "tenant_access_denied": RejectRow("Access denied", error=ForbiddenError),
        "tenant_inactive": RejectRow("Tenant inactive. Contact support.", error=ForbiddenError),
        # 413 PAYLOAD_TOO_LARGE: no exception family covers a body-limit refusal.
        "payload_too_large": RejectRow(
            "Request body too large", status=413, headers_builder=_max_size_headers
        ),
        # 400 INVALID_INPUT: distinct from ValidationError's 400 VALIDATION_ERROR,
        # and no family covers it — hence the explicit status.
        "invalid_input": RejectRow("{message}", status=400),
        "attack_blocked": RejectRow("Bad Request - Attack pattern detected", error=DomainValidationError),
        "attack_banned": RejectRow("Forbidden - Attack detected and IP banned", error=ForbiddenError),
        "bad_bot": RejectRow("Forbidden - Bad bot detected", error=ForbiddenError),
        "blacklisted_ip": RejectRow("Forbidden - IP is blacklisted", error=ForbiddenError),
    }
)


@overload
def middleware_reject(
    kind: _ParamlessKind, *, headers: Mapping[str, str] | None = None
) -> JSONResponse: ...


@overload
def middleware_reject(
    kind: Literal["banned_ip"], *, headers: Mapping[str, str] | None = None, **params: Unpack[_BannedIpParams]
) -> JSONResponse: ...


@overload
def middleware_reject(
    kind: Literal["auto_banned"], *, headers: Mapping[str, str] | None = None, **params: Unpack[_AutoBannedParams]
) -> JSONResponse: ...


@overload
def middleware_reject(
    kind: Literal["tenant_header_missing"],
    *,
    headers: Mapping[str, str] | None = None,
    **params: Unpack[_TenantHeaderMissingParams],
) -> JSONResponse: ...


@overload
def middleware_reject(
    kind: Literal["payload_too_large"], *, headers: Mapping[str, str] | None = None, **params: Unpack[_MaxSizeParams]
) -> JSONResponse: ...


@overload
def middleware_reject(
    kind: Literal["invalid_input"], *, headers: Mapping[str, str] | None = None, **params: Unpack[_MessageParams]
) -> JSONResponse: ...


def middleware_reject(
    kind: RejectKind,
    *,
    headers: Mapping[str, str] | None = None,
    **params: Any,
) -> JSONResponse:
    """Resolve a rejection row from the catalog and build its envelope.

    The pre-route adapter: native ASGI middleware runs before routing, so it
    cannot *raise* a domain exception into the app's handler — but it must not
    hand-roll envelopes either. So it resolves the row and reads the family's
    own declared status/code (see :class:`RejectRow`) instead of restating them.

    Guards call this with a kind + typed primitives instead of hand-building
    code/message/status/headers at each call site. Unknown kinds fail loud
    (``ValueError``) — a typo must never become a shapeless 500.
    ``headers`` merges over the row's own headers (for verdict passthrough).
    """
    try:
        row = REJECT_CATALOG[kind]
    except KeyError:
        raise ValueError(f"Unknown middleware reject kind: {kind!r}") from None
    code, status = row.resolve()
    message = row.message.format(**params)
    built = dict(row.headers_builder(params)) if row.headers_builder is not None else {}
    if headers is not None:
        built.update(headers)
    return middleware_error_response(code=code, message=message, status_code=status, headers=built or None)


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

    Registered *after* the framework handlers; order does not decide the
    winner (Starlette walks the MRO and takes the most specific registered
    class), but keeping it last documents the intent: the framework handlers
    are the overrides, this is the default for the whole domain tree.
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
