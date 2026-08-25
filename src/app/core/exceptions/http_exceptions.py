from fastcrud.exceptions.http_exceptions import (
    BadRequestException,
    CustomException,
    DuplicateValueException,
    ForbiddenException,
    NotFoundException,
    RateLimitException,
    UnauthorizedException,
    UnprocessableEntityException,
)


class TenantContextError(CustomException):
    """Raised when a tenant-scoped query is attempted without active tenant context."""

    def __init__(
        self,
        detail: str = "Tenant context required for tenant-scoped operation",
        status_code: int = 403,
    ) -> None:
        # H3: unified class — mirror the legacy _base/ForbiddenError attribute
        # surface (.message/.reason) so core/errors.py's handler keeps working.
        self.message = detail
        self.reason = "tenant_context_error"
        super().__init__(detail=detail, status_code=status_code)


__all__ = [
    "CustomException",
    "BadRequestException",
    "NotFoundException",
    "ForbiddenException",
    "UnauthorizedException",
    "UnprocessableEntityException",
    "DuplicateValueException",
    "RateLimitException",
    "TenantContextError",
]
