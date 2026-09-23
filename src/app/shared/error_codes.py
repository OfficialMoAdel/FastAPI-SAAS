"""Standard API error codes — zero-dependency leaf.

This module exists so that the exception leaf
(:mod:`src.app.shared.exceptions`) can declare its own HTTP mapping
without importing :mod:`src.app.core.errors`.

That import would be a cycle: ``core.errors`` imports
``core.exceptions.cache_exceptions`` at module scope, and
``cache_exceptions`` imports ``shared.exceptions``. Keeping the code
catalog here lets both sides depend on a leaf instead.

``src.app.core.errors`` re-exports ``ErrorCode``, so existing
``from src.app.core.errors import ErrorCode`` sites keep working.
"""


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

    # Request payload errors
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

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


__all__ = ["ErrorCode", "status_to_error_code"]


# HTTP status -> envelope error code. Lives here (not in core) because both
# the framework HTTP handler in core and the auth exceptions need it, and the
# exception leaf may not import core.
_STATUS_TO_ERROR_CODE: dict[int, str] = {
    400: ErrorCode.INVALID_INPUT,
    401: ErrorCode.UNAUTHORIZED,
    402: ErrorCode.PAYMENT_REQUIRED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    410: ErrorCode.GONE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    431: ErrorCode.INVALID_INPUT,
    # I12: 413 previously fell through to INTERNAL_ERROR — the body-limit
    # middleware (and any StarletteHTTPException(413)) mislabeled itself.
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    500: ErrorCode.INTERNAL_ERROR,
    501: ErrorCode.NOT_IMPLEMENTED,
    502: ErrorCode.BAD_GATEWAY,
    503: ErrorCode.SERVICE_UNAVAILABLE,
    504: ErrorCode.SERVICE_UNAVAILABLE,
    507: ErrorCode.STORAGE_QUOTA_EXCEEDED,
}


def status_to_error_code(status_code: int) -> str:
    """Map an HTTP status code to an envelope error code.

    Q-M5: 501 now maps to NOT_IMPLEMENTED (previously 501 responses had no
    mapping and fell through to INTERNAL_ERROR, and the one explicit 501
    handler mislabeled itself SERVICE_UNAVAILABLE); 502 maps to the generic
    BAD_GATEWAY (previously every 502 was mislabeled STORAGE_BACKEND_ERROR,
    which describes exactly one storage handler, not arbitrary gateway
    failures). Item 7: 504 (SSO provider timeout) maps to
    SERVICE_UNAVAILABLE.
    """
    return _STATUS_TO_ERROR_CODE.get(status_code, ErrorCode.INTERNAL_ERROR)
