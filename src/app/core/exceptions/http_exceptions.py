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

from src.app.shared.exceptions import TenantContextError as _SharedTenantContextError

TenantContextError = _SharedTenantContextError

# Item 7: TenantContextError moved to src.app.shared.exceptions (domain tree).
# This module-level alias preserves ``from ...http_exceptions import
# TenantContextError`` for auth deps/RBAC/CRUD importers (single-tree alias,
# not a second tree — isinstance checks and the error table resolve the
# shared class). Prefer importing from ``src.app.shared.exceptions``.

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
