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


def setup_exception_handlers(app: FastAPI) -> None:
    # H7 FIX: Deferred domain imports — breaks core -> modules cycle.
    from src.app.modules._base.exceptions import (  # type: ignore
        DuplicateError,
        ForbiddenError,
        ModelNotFoundError,
        RateLimitExceededError,
        ServiceUnavailableError,
        StripeError,
        TenantContextError,
    )
    from src.app.modules._base.exceptions import (
        ValidationError as BaseValidationError,
    )
    from src.app.modules.ai.unified_errors import SessionLimitExceededError  # type: ignore
    from src.app.modules.billing.exceptions import PaymentRequiredError  # type: ignore

    """Register global exception handlers on the FastAPI app.

    Call this after creating the app:
        app = FastAPI()
        setup_exception_handlers(app)
    """

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


def _status_to_error_code(status_code: int) -> str:
    """Map HTTP status code to error code."""
    mapping = {
        400: ErrorCode.INVALID_INPUT,
        401: ErrorCode.UNAUTHORIZED,
        402: ErrorCode.PAYMENT_REQUIRED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.CONFLICT,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.INTERNAL_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }
    return mapping.get(status_code, ErrorCode.INTERNAL_ERROR)
