"""DEPRECATED (Q-M1/A-M1): compatibility shim over ``src.app.shared.exceptions``.

The canonical home for domain exceptions is ``src.app.shared.exceptions``;
this module only re-exports them (plus the centralized
``TenantContextError`` from core) so legacy imports keep working.
New code MUST import from ``src.app.shared.exceptions`` directly.

Boundary rule (A-M1): this module-foundation must never import from a
feature module (e.g. ``modules/billing``, ``modules/tiers``) — feature
domains depend on the base, never the reverse. Only foundation leaves
(``shared``, ``core``) may appear below.
"""

from __future__ import annotations

from src.app.core.exceptions.http_exceptions import TenantContextError
from src.app.shared.exceptions import (
    AppException,
    DuplicateError,
    ForbiddenError,
    ModelNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    StripeError,
    UnauthorizedError,
    ValidationError,
)

__all__ = [
    "AppException",
    "DuplicateError",
    "ForbiddenError",
    "ModelNotFoundError",
    "RateLimitExceededError",
    "ServiceUnavailableError",
    "StripeError",
    "TenantContextError",
    "UnauthorizedError",
    "ValidationError",
]
