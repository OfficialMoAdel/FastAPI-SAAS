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
        self, detail: str = "Tenant context required for tenant-scoped operation", status_code: int = 500
    ) -> None:
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
