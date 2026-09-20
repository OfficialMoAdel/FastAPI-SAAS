"""FROZEN legacy error tree (P2).

This module re-exports fastcrud's HTTP-flavored exceptions. New code must
raise ``src.app.shared.exceptions`` domain errors (AppException tree) —
they carry the canonical status/code mapping in core/errors.py. Do not
add new exception types here; existing import sites may keep working
until they are migrated.
"""

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
