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

Handlers are registered per domain via ``register_*_handlers`` helpers;
``setup_exception_handlers`` is the thin composer that wires them all.
"""

from typing import Any

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
    headers: dict[str, str] | None = None,
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


def _register_base_handlers(app: FastAPI) -> None:
    """Shared _base domain handlers (CRUD, validation, tenancy, availability)."""
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules._base.exceptions import (  # type: ignore
        DuplicateError,
        ForbiddenError,
        ModelNotFoundError,
        RateLimitExceededError,
        ServiceUnavailableError,
        StripeError,
        TenantContextError,
        UnauthorizedError,
    )
    from src.app.modules._base.exceptions import (
        ValidationError as BaseValidationError,
    )

    @app.exception_handler(ModelNotFoundError)  # type: ignore
    async def model_not_found_handler(request: Request, exc: ModelNotFoundError) -> JSONResponse:  # type: ignore
        """Handle ModelNotFoundError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Resource not found",
            model=exc.model_name,
            identifier=str(exc.identifier),
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.NOT_FOUND,
            message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
            details={"model": exc.model_name, "identifier": str(exc.identifier)},
            request_id=request_id,
        )

    @app.exception_handler(DuplicateError)  # type: ignore
    async def duplicate_error_handler(request: Request, exc: DuplicateError) -> JSONResponse:  # type: ignore
        """Handle DuplicateError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Duplicate resource",
            field=exc.field,
            value=str(exc.value),
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.ALREADY_EXISTS,
            message=str(exc),
            status_code=status.HTTP_409_CONFLICT,
            details={"field": exc.field, "value": str(exc.value)},
            request_id=request_id,
        )

    @app.exception_handler(ForbiddenError)  # type: ignore
    async def forbidden_error_handler(request: Request, exc: ForbiddenError) -> JSONResponse:  # type: ignore
        """Handle ForbiddenError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Forbidden action",
            reason=exc.reason,
            message=exc.message,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.FORBIDDEN,
            message=exc.message,
            status_code=status.HTTP_403_FORBIDDEN,
            details={"reason": exc.reason},
            request_id=request_id,
        )

    @app.exception_handler(BaseValidationError)  # type: ignore
    async def base_validation_error_handler(request: Request, exc: BaseValidationError) -> JSONResponse:  # type: ignore
        """Handle ValidationError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Validation error",
            message=exc.message,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.VALIDATION_ERROR,
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )

    @app.exception_handler(RateLimitExceededError)  # type: ignore
    async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceededError) -> JSONResponse:  # type: ignore
        """Handle RateLimitExceededError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Rate limit exceeded",
            message=exc.message,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.RATE_LIMITED,
            message=str(exc),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            request_id=request_id,
        )

    @app.exception_handler(TenantContextError)  # type: ignore
    async def tenant_context_error_handler(request: Request, exc: TenantContextError) -> JSONResponse:  # type: ignore
        """Handle TenantContextError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Tenant context error",
            reason=exc.reason,
            message=exc.message,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.FORBIDDEN,
            message=exc.message,
            status_code=status.HTTP_403_FORBIDDEN,
            details={"reason": exc.reason},
            request_id=request_id,
        )

    @app.exception_handler(ServiceUnavailableError)  # type: ignore
    async def service_unavailable_error_handler(request: Request, exc: ServiceUnavailableError) -> JSONResponse:  # type: ignore
        """Handle ServiceUnavailableError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.error(
            "Service unavailable",
            message=exc.message,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=exc.message,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            request_id=request_id,
        )

    @app.exception_handler(StripeError)  # type: ignore
    async def stripe_error_handler(request: Request, exc: StripeError) -> JSONResponse:  # type: ignore
        """Handle StripeError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Stripe error",
            message=exc.message,
            status_code=exc.status_code,
            request_id=request_id,
            path=request.url.path,
        )

        return create_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE if exc.status_code >= 500 else ErrorCode.INVALID_INPUT,
            message=exc.message,
            status_code=exc.status_code,
            request_id=request_id,
        )

    @app.exception_handler(UnauthorizedError)  # type: ignore
    async def unauthorized_error_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:  # type: ignore
        """Handle UnauthorizedError from _base exceptions."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "Unauthorized error",
            message=exc.message,
            reason=getattr(exc, "reason", "unauthorized"),
            request_id=request_id,
            path=request.url.path,
        )
        return create_error_response(
            code=ErrorCode.UNAUTHORIZED,
            message=exc.message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            request_id=request_id,
        )


def _register_auth_handlers(app: FastAPI) -> None:
    """Auth domain handlers."""
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules.auth.errors import AuthError  # type: ignore

    @app.exception_handler(AuthError)  # type: ignore
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:  # type: ignore
        """Handle AuthError from auth domain."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "Auth domain error",
            detail=exc.detail,
            status_code=exc.status_code,
            request_id=request_id,
            path=request.url.path,
        )
        code = _status_to_error_code(exc.status_code)
        return create_error_response(
            code=code,
            message=exc.detail,
            status_code=exc.status_code,
            request_id=request_id,
        )

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


def _register_billing_handlers(app: FastAPI) -> None:
    """Billing domain handlers (payments + webhooks)."""
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules.billing.exceptions import (  # type: ignore
        PaymentRequiredError,
        WebhookNotConfiguredError,
        WebhookVerificationError,
    )

    @app.exception_handler(PaymentRequiredError)  # type: ignore
    async def payment_required_handler(request: Request, exc: PaymentRequiredError) -> JSONResponse:  # type: ignore
        """Handle PaymentRequiredError for billing/payment failures.

        Returns HTTP 402 Payment Required with payment error details.
        """
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Payment required",
            error_code=exc.error_code,
            decline_code=exc.decline_code,
            request_id=request_id,
            path=request.url.path,
        )

        details: dict[str, Any] = {"error_code": exc.error_code}
        if exc.decline_code:
            details["decline_code"] = exc.decline_code

        return create_error_response(
            code=ErrorCode.PAYMENT_REQUIRED,
            message=str(exc),
            status_code=402,
            details=details,
            request_id=request_id,
        )

    @app.exception_handler(WebhookVerificationError)  # type: ignore
    async def webhook_verification_error_handler(request: Request, exc: WebhookVerificationError) -> JSONResponse:  # type: ignore
        """Handle WebhookVerificationError from billing module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("Webhook verification error", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.INVALID_INPUT,
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )

    @app.exception_handler(WebhookNotConfiguredError)  # type: ignore
    async def webhook_not_configured_handler(request: Request, exc: WebhookNotConfiguredError) -> JSONResponse:  # type: ignore
        """Handle WebhookNotConfiguredError from billing module."""
        request_id = getattr(request.state, "request_id", None)
        logger.error("Webhook secret not configured", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=exc.message,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            request_id=request_id,
        )


def _register_storage_handlers(app: FastAPI) -> None:
    """Storage domain handlers."""
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules.storage.errors import (  # type: ignore
        FileEntityNotFoundError,
        FileTypeNotAllowedError,
        InvalidCredentialsError,
        QuotaExceededError,
        StorageBackendError,
        StorageNotConfiguredError,
    )

    @app.exception_handler(FileEntityNotFoundError)  # type: ignore
    async def file_not_found_handler(request: Request, exc: FileEntityNotFoundError) -> JSONResponse:  # type: ignore
        """Handle FileEntityNotFoundError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("File entity not found", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.NOT_FOUND,
            message="File not found",
            status_code=status.HTTP_404_NOT_FOUND,
            request_id=request_id,
        )

    @app.exception_handler(StorageNotConfiguredError)  # type: ignore
    async def storage_not_configured_handler(request: Request, exc: StorageNotConfiguredError) -> JSONResponse:  # type: ignore
        """Handle StorageNotConfiguredError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("Storage not configured", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.INVALID_INPUT,
            message="Storage not configured for this tenant.",
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )

    @app.exception_handler(FileTypeNotAllowedError)  # type: ignore
    async def file_type_not_allowed_handler(request: Request, exc: FileTypeNotAllowedError) -> JSONResponse:  # type: ignore
        """Handle FileTypeNotAllowedError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("File type not allowed", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.INVALID_INPUT,
            message="File type not allowed.",
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )

    @app.exception_handler(QuotaExceededError)  # type: ignore
    async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:  # type: ignore
        """Handle QuotaExceededError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("Storage quota exceeded", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.STORAGE_QUOTA_EXCEEDED,
            message="Storage quota exceeded.",
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            request_id=request_id,
        )

    @app.exception_handler(StorageBackendError)  # type: ignore
    async def storage_backend_error_handler(request: Request, exc: StorageBackendError) -> JSONResponse:  # type: ignore
        """Handle StorageBackendError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        # P0 FIX: never echo provider internals (paths, buckets, credentials
        # fragments) to the client; log detail server-side with sanitizer.
        logger.error("Storage backend error", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.STORAGE_BACKEND_ERROR,
            message="Storage provider error during file operation.",
            status_code=status.HTTP_502_BAD_GATEWAY,
            request_id=request_id,
        )

    @app.exception_handler(InvalidCredentialsError)  # type: ignore
    async def invalid_credentials_error_handler(request: Request, exc: InvalidCredentialsError) -> JSONResponse:  # type: ignore
        """Handle InvalidCredentialsError from storage module."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning("Invalid storage credentials", error=str(exc), request_id=request_id, path=request.url.path)
        return create_error_response(
            code=ErrorCode.INVALID_INPUT,
            message="Invalid storage provider credentials.",
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )


def _register_ai_handlers(app: FastAPI) -> None:
    """AI domain handlers (session limits)."""
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules.ai.unified_errors import SessionLimitExceededError  # type: ignore

    @app.exception_handler(SessionLimitExceededError)  # type: ignore
    async def session_limit_exceeded_handler(request: Request, exc: SessionLimitExceededError) -> JSONResponse:  # type: ignore
        """Handle SessionLimitExceededError for AI session limits.

        Returns HTTP 429 Too Many Requests when session limit is exceeded.
        """
        request_id = getattr(request.state, "request_id", None)

        logger.warning(
            "Session limit exceeded",
            tenant_id=exc.tenant_id,
            limit=exc.limit,
            current_count=exc.current_count,
            request_id=request_id,
            path=request.url.path,
        )

        details: dict[str, Any] = {
            "tenant_id": exc.tenant_id,
            "limit": exc.limit,
        }
        if exc.current_count is not None:
            details["current_count"] = exc.current_count

        return create_error_response(
            code=ErrorCode.SESSION_LIMIT_EXCEEDED,
            message=str(exc),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details=details,
            request_id=request_id,
        )


def setup_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app.

    Thin composer over the per-domain ``register_*`` helpers above.
    Call this after creating the app:
        app = FastAPI()
        setup_exception_handlers(app)
    """
    _register_framework_handlers(app)
    _register_base_handlers(app)
    _register_auth_handlers(app)
    _register_billing_handlers(app)
    _register_storage_handlers(app)
    _register_ai_handlers(app)


def _status_to_error_code(status_code: int) -> str:
    """Map HTTP status code to error code."""
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
        502: ErrorCode.STORAGE_BACKEND_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
        507: ErrorCode.STORAGE_QUOTA_EXCEEDED,
    }
    return mapping.get(status_code, ErrorCode.INTERNAL_ERROR)
