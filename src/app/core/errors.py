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

Handlers are registered from the domain ``EXCEPTION_HANDLERS`` table
(via ``build_exception_table`` + the ``_make_table_handler`` factory);
``setup_exception_handlers`` is the thin composer that wires the framework
handlers, the table, and the global catch-all.
"""

from collections.abc import Callable, Coroutine, Mapping
from typing import Any, NamedTuple

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from structlog import get_logger

from src.app.core.exceptions.cache_exceptions import CacheException

logger = get_logger(__name__)


# Standard error codes
class ErrorCode:
    """Standardized error codes for API responses."""

    # Authentication/Authorization
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_TOKEN = "INVALID_TOKEN"

    # Resource errors
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    CONFLICT = "CONFLICT"
    GONE = "GONE"

    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"

    # Rate limiting
    RATE_LIMITED = "RATE_LIMITED"

    # Payment errors
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    PAYMENT_FAILED = "PAYMENT_FAILED"

    # Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    # Q-M5: 501 (not implemented) and 502 (bad gateway) previously had no
    # dedicated codes — 501 responses were mislabeled SERVICE_UNAVAILABLE
    # and every 502 was mislabeled STORAGE_BACKEND_ERROR.
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    BAD_GATEWAY = "BAD_GATEWAY"

    # Tenant errors
    TENANT_NOT_FOUND = "TENANT_NOT_FOUND"
    TENANT_SUSPENDED = "TENANT_SUSPENDED"

    # Resource limit errors
    SESSION_LIMIT_EXCEEDED = "SESSION_LIMIT_EXCEEDED"
    STORAGE_QUOTA_EXCEEDED = "STORAGE_QUOTA_EXCEEDED"
    STORAGE_BACKEND_ERROR = "STORAGE_BACKEND_ERROR"


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
        error_inner["details"] = details

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

        code = _status_to_error_code(exc.status_code)

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


class _HandlerSpec(NamedTuple):
    """One row of the domain exception-handler table.

    ``status``/``code``/``message`` accept either a literal or a callable
    resolving the value from the exception instance (StripeError and
    AuthError carry their own status code). ``details`` is None or a
    callable building the response ``details`` payload. ``log_message`` /
    ``log_error`` preserve the per-handler log output.
    """

    exc_class: type[Exception]
    status: int | Callable[[Any], int]
    code: str | Callable[[Any], str]
    message: str | Callable[[Any], str]
    details: Callable[[Any], dict[str, Any] | None] | None
    log_message: str
    log_error: bool
    headers: Callable[[Any], dict[str, str] | None] | None = None


def _model_not_found_details(exc: Any) -> dict[str, Any]:
    """Build the ``details`` payload for ModelNotFoundError."""
    return {
        "model": getattr(exc, "model_name", type(exc).__name__),
        "identifier": str(getattr(exc, "identifier", "")),
    }


def _duplicate_details(exc: Any) -> dict[str, Any]:
    """Build the ``details`` payload for DuplicateError."""
    return {"field": getattr(exc, "field", ""), "value": str(getattr(exc, "value", ""))}


def _reason_details(exc: Any) -> dict[str, Any]:
    """Build the ``details`` payload for ForbiddenError / TenantContextError."""
    return {"reason": getattr(exc, "reason", str(exc))}


def _payment_details(exc: Any) -> dict[str, Any]:
    """Build the ``details`` payload for PaymentRequiredError."""
    details: dict[str, Any] = {"error_code": getattr(exc, "error_code", "")}
    if getattr(exc, "decline_code", None):
        details["decline_code"] = exc.decline_code
    return details


def _session_limit_details(exc: Any) -> dict[str, Any]:
    """Build the ``details`` payload for SessionLimitExceededError."""
    details: dict[str, Any] = {
        "tenant_id": getattr(exc, "tenant_id", None),
        "limit": getattr(exc, "limit", None),
    }
    if getattr(exc, "current_count", None) is not None:
        details["current_count"] = exc.current_count
    return details


def _stripe_code(exc: Any) -> str:
    """Resolve the error code for StripeError from its status code."""
    return ErrorCode.SERVICE_UNAVAILABLE if exc.status_code >= 500 else ErrorCode.INVALID_INPUT


def _auth_code(exc: Any) -> str:
    """Resolve the error code for AuthError from its status code."""
    return _status_to_error_code(exc.status_code)


def _make_table_handler(spec: _HandlerSpec) -> Callable[[Request, Any], Coroutine[Any, Any, JSONResponse]]:
    """Generic handler factory: resolve one table row into a FastAPI handler."""
    async def _handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        status_spec = spec.status
        code_spec = spec.code
        message_spec = spec.message
        resolved_status = status_spec(exc) if callable(status_spec) else status_spec
        resolved_code = code_spec(exc) if callable(code_spec) else code_spec
        resolved_message = message_spec(exc) if callable(message_spec) else message_spec
        resolved_details = spec.details(exc) if spec.details is not None else None
        resolved_headers = spec.headers(exc) if spec.headers is not None else None
        log = logger.error if spec.log_error else logger.warning
        log(spec.log_message, error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=resolved_code,
            message=resolved_message,
            status_code=resolved_status,
            details=resolved_details,
            request_id=request_id,
            headers=resolved_headers,
        )

    return _handler


def build_exception_table() -> list[_HandlerSpec]:
    """Build the domain exception-handler table.

    H7: imports are deferred (function scope) to break the core -> modules
    cycle — core must never import feature modules at module scope.
    Response codes/messages are identical to the pre-table handlers.
    """
    from src.app.core.exceptions.http_exceptions import TenantContextError
    from src.app.modules.ai.unified_errors import SessionLimitExceededError
    from src.app.modules.auth.errors import AuthError
    from src.app.modules.billing.exceptions import (
        PaymentRequiredError,
        WebhookNotConfiguredError,
        WebhookVerificationError,
    )
    from src.app.modules.storage.errors import (
        FileEntityNotFoundError,
        FileTypeNotAllowedError,
        InvalidCredentialsError,
        QuotaExceededError,
        StorageBackendError,
        StorageNotConfiguredError,
    )
    from src.app.shared.exceptions import (
        DuplicateError,
        ForbiddenError,
        ModelNotFoundError,
        RateLimitExceededError,
        ServiceUnavailableError,
        StripeError,
        UnauthorizedError,
    )
    from src.app.shared.exceptions import (
        ValidationError as BaseValidationError,
    )

    EXCEPTION_HANDLERS: list[_HandlerSpec] = [
        # Shared domain errors (canonical home: src.app.shared.exceptions).
        _HandlerSpec(
            ModelNotFoundError,
            status.HTTP_404_NOT_FOUND,
            ErrorCode.NOT_FOUND,
            str,
            _model_not_found_details,
            "Resource not found",
            False,
        ),
        _HandlerSpec(
            DuplicateError,
            status.HTTP_409_CONFLICT,
            ErrorCode.ALREADY_EXISTS,
            str,
            _duplicate_details,
            "Duplicate resource",
            False,
        ),
        _HandlerSpec(
            ForbiddenError,
            status.HTTP_403_FORBIDDEN,
            ErrorCode.FORBIDDEN,
            lambda exc: exc.message,
            _reason_details,
            "Forbidden action",
            False,
        ),
        _HandlerSpec(
            BaseValidationError,
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.VALIDATION_ERROR,
            str,
            None,
            "Validation error",
            False,
        ),
        _HandlerSpec(
            RateLimitExceededError,
            status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCode.RATE_LIMITED,
            str,
            None,
            "Rate limit exceeded",
            False,
            headers=lambda exc: {"Retry-After": str(getattr(exc, "retry_after", 60))},
        ),
        _HandlerSpec(
            TenantContextError,
            status.HTTP_403_FORBIDDEN,
            ErrorCode.FORBIDDEN,
            lambda exc: exc.message,
            _reason_details,
            "Tenant context error",
            False,
        ),
        _HandlerSpec(
            ServiceUnavailableError,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ErrorCode.SERVICE_UNAVAILABLE,
            lambda exc: exc.message,
            None,
            "Service unavailable",
            True,
        ),
        _HandlerSpec(
            StripeError,
            lambda exc: exc.status_code,
            _stripe_code,
            lambda exc: exc.message,
            None,
            "Stripe error",
            False,
        ),
        _HandlerSpec(
            UnauthorizedError,
            status.HTTP_401_UNAUTHORIZED,
            ErrorCode.UNAUTHORIZED,
            lambda exc: exc.message,
            None,
            "Unauthorized error",
            False,
        ),
        # Auth domain (canonical home: src.app.modules.auth.errors).
        _HandlerSpec(
            AuthError,
            lambda exc: exc.status_code,
            _auth_code,
            lambda exc: exc.detail,
            None,
            "Auth domain error",
            False,
        ),
        # Billing domain (canonical home: src.app.modules.billing.exceptions).
        _HandlerSpec(
            PaymentRequiredError,
            status.HTTP_402_PAYMENT_REQUIRED,
            ErrorCode.PAYMENT_REQUIRED,
            str,
            _payment_details,
            "Payment required",
            False,
        ),
        _HandlerSpec(
            WebhookVerificationError,
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.INVALID_INPUT,
            str,
            None,
            "Webhook verification error",
            False,
        ),
        _HandlerSpec(
            WebhookNotConfiguredError,
            status.HTTP_501_NOT_IMPLEMENTED,
            ErrorCode.NOT_IMPLEMENTED,
            lambda exc: exc.message,
            None,
            "Webhook secret not configured",
            True,
        ),
        # Storage domain (canonical home: src.app.modules.storage.errors).
        _HandlerSpec(
            FileEntityNotFoundError,
            status.HTTP_404_NOT_FOUND,
            ErrorCode.NOT_FOUND,
            "File not found",
            None,
            "File entity not found",
            False,
        ),
        _HandlerSpec(
            StorageNotConfiguredError,
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.INVALID_INPUT,
            "Storage not configured for this tenant.",
            None,
            "Storage not configured",
            False,
        ),
        _HandlerSpec(
            FileTypeNotAllowedError,
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.INVALID_INPUT,
            "File type not allowed.",
            None,
            "File type not allowed",
            False,
        ),
        _HandlerSpec(
            QuotaExceededError,
            status.HTTP_507_INSUFFICIENT_STORAGE,
            ErrorCode.STORAGE_QUOTA_EXCEEDED,
            "Storage quota exceeded.",
            None,
            "Storage quota exceeded",
            False,
        ),
        _HandlerSpec(
            StorageBackendError,
            status.HTTP_502_BAD_GATEWAY,
            ErrorCode.STORAGE_BACKEND_ERROR,
            "Storage provider error during file operation.",
            None,
            "Storage backend error",
            True,
        ),
        _HandlerSpec(
            InvalidCredentialsError,
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.INVALID_INPUT,
            "Invalid storage provider credentials.",
            None,
            "Invalid storage credentials",
            False,
        ),
        # AI domain (canonical home: src.app.modules.ai.unified_errors).
        _HandlerSpec(
            SessionLimitExceededError,
            status.HTTP_429_TOO_MANY_REQUESTS,
            ErrorCode.SESSION_LIMIT_EXCEEDED,
            str,
            _session_limit_details,
            "Session limit exceeded",
            False,
        ),
    ]
    return EXCEPTION_HANDLERS


def _register_table_handlers(app: FastAPI) -> None:
    """Register every domain exception in the table via the generic factory."""
    for spec in build_exception_table():
        app.exception_handler(spec.exc_class)(_make_table_handler(spec))  # type: ignore


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

    Thin composer over the framework handlers, the domain
    ``EXCEPTION_HANDLERS`` table (see ``build_exception_table``), and the
    global catch-all. Call this after creating the app:
        app = FastAPI()
        setup_exception_handlers(app)
    """
    _register_framework_handlers(app)
    _register_table_handlers(app)
    _register_global_handler(app)


def _status_to_error_code(status_code: int) -> str:
    """Map HTTP status code to error code.

    Q-M5: 501 now maps to NOT_IMPLEMENTED (previously 501 responses had no
    mapping and fell through to INTERNAL_ERROR, and the one explicit 501
    handler mislabeled itself SERVICE_UNAVAILABLE); 502 maps to the generic
    BAD_GATEWAY (previously every 502 was mislabeled STORAGE_BACKEND_ERROR,
    which describes exactly one storage handler, not arbitrary gateway
    failures).
    """
    mapping = {
        400: ErrorCode.INVALID_INPUT,
        401: ErrorCode.UNAUTHORIZED,
        402: ErrorCode.PAYMENT_REQUIRED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        410: ErrorCode.GONE,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.INTERNAL_ERROR,
        501: ErrorCode.NOT_IMPLEMENTED,
        502: ErrorCode.BAD_GATEWAY,
        503: ErrorCode.SERVICE_UNAVAILABLE,
        507: ErrorCode.STORAGE_QUOTA_EXCEEDED,
    }
    return mapping.get(status_code, ErrorCode.INTERNAL_ERROR)
