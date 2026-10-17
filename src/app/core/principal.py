"""Principal seam — the ONE typed identity crossing the module boundary.

This module is the canonical home of :class:`AuthenticatedUser` and
:class:`AuthContext`, plus the **outcome table** that decides what a
refused principal resolution raises. It lives in core (not ``api.deps``)
deliberately: ``api.deps.auth`` imports the module facades (``users``,
``api_keys``), so services importing the principal from ``api.deps``
would close an import cycle — the reason ``users/service.py`` and
``webhooks/service.py`` carried ``TYPE_CHECKING`` workarounds. Importing
from here is cycle-free in both directions.

Layers
------
- ``api.deps.auth`` re-exports :class:`AuthenticatedUser`,
  :class:`AuthContext` and :func:`get_auth_context` unchanged, so existing
  ``from src.app.api.deps[.auth] import ...`` sites keep working; new code
  prefers this module.
- ``RequestContext.user`` carries this type; services take it directly
  instead of ``Any`` / ``dict | AuthenticatedUser`` and read attributes
  instead of re-deriving via the dict-or-object shim.
- :func:`get_user_attr` (``core.utils.user_utils``) remains for CRUD-row
  access (FastCRUD ``schema_to_select`` dicts / ORM objects) — it is no
  longer the principal accessor.

Why the seam is inverted
------------------------
``core.rbac.dependencies`` needs the resolved principal, but core must not
import ``api.deps`` (H-5: core must not depend on modules or the api
layer). :func:`get_auth_context` therefore lives **here**, delegating to a
provider that ``api.deps.auth`` registers at import time — the same
DIP-registry shape ``core.security.jwt_service`` already uses for its
token-version / user-tenant lookups (PR-12). The dependency object stays
identical across the re-export, so ``app.dependency_overrides`` in tests
keeps matching without a single change.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger
from src.app.core.schemas import TokenData
from src.app.core.security.jwt_service import oauth2_scheme
from src.app.core.tenancy.context import TenantContext
from src.app.shared.exceptions import (
    AppException,
    ForbiddenError,
    ServiceUnavailableError,
    TenantContextError,
    UnauthorizedError,
)

logger = get_logger(__name__)


class AuthenticatedUser(BaseModel):
    """Strict typed user model for request dependencies.

    Provides IDE autocomplete while maintaining backward compatibility
    with dict-based access via __getitem__.
    """

    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True, from_attributes=True)

    id: int
    uuid: UUID | str | None = None
    email: str
    username: str | None = None
    # F1 profile-projection fields: the bearer-auth construction path
    # (api.deps.auth._create_auth_context) builds the principal from the
    # ``get_user_auth_info`` payload, which already carries every
    # ``UserRead`` input for the SAME row the profile cache would
    # re-serve. Carrying them here lets ``resolve_current_profile``
    # serialize the principal directly on ``GET /users/me`` (the 80%
    # hot path) instead of paying an extra Redis GET — or a DB SELECT on
    # cache miss — per request. All fields are optional so principal
    # constructions that genuinely lack them (tests, partial payloads)
    # keep working and fall back to the profile-cache load path.
    name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_email_verified: bool = False
    is_superuser: bool = False
    is_active: bool = True
    is_deleted: bool = False
    tier_id: int | None = None
    tenant_id: UUID | str | int | None = None
    profile_image_url: str | None = None
    token_version: int = 0
    tier: dict[str, Any] | None = None
    api_key_id: str | int | None = None
    api_key_scopes: list[str] | None = None

    # Q-M2: dict-compat shim (__getitem__/get/keys/__contains__/__len__)
    # deleted. Principal call sites use attribute access; CRUD-row dicts
    # go through get_user_attr. Do NOT re-add subscript access — it hid
    # type errors on the hottest auth path.


class AuthContext(BaseModel):
    """The resolved principal for one request, inside the tenant it acts in.

    ``token_data`` is either the ``TokenData`` returned by ``verify_token``
    or the raw cached JWT payload dict (same claim fields as keys);
    ``tenant`` is the lightweight :class:`TenantContext` (or ``None`` when
    no tenant context was bound at all).
    """

    user: AuthenticatedUser
    token_data: TokenData | dict[str, Any] | None
    tenant: TenantContext | None


class PrincipalPresence(StrEnum):
    """Whether a credential must be presented.

    ``REQUIRED`` raises when nothing was presented; ``OPTIONAL`` returns
    ``None``. Neither relaxes a *presented* credential that fails to
    verify — that is always a refusal (C3).
    """

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class PrincipalPolicy:
    """Everything a caller must state to resolve a principal.

    One named value instead of a positional boolean: ``strict=True`` /
    ``strict=False`` at a call site said nothing about *what* was being
    relaxed, and the two modes differ in their error modes, not just in
    their return type.
    """

    presence: PrincipalPresence = PrincipalPresence.REQUIRED


REQUIRED = PrincipalPolicy(presence=PrincipalPresence.REQUIRED)
OPTIONAL = PrincipalPolicy(presence=PrincipalPresence.OPTIONAL)


class DenialReason(StrEnum):
    """Why a principal resolution was refused — the keys of :data:`DENIAL_TABLE`."""

    NO_CREDENTIAL = "no_credential"
    INVALID_CREDENTIAL = "invalid_credential"
    INVALID_API_KEY = "invalid_api_key"
    API_KEY_TENANT_MISMATCH = "api_key_tenant_mismatch"
    REVOKED_TOKEN = "revoked_token"
    REVOKED_SESSION = "revoked_session"
    INACTIVE_USER = "inactive_user"
    INACTIVE_SUPERUSER = "inactive_superuser"
    DELETED_USER = "deleted_user"
    STALE_TOKEN_VERSION = "stale_token_version"
    TENANT_MISMATCH = "tenant_mismatch"
    TENANT_CONTEXT_MISSING = "tenant_context_missing"
    SCOPE_UNVERIFIABLE = "scope_unverifiable"
    SCOPE_MISSING = "scope_missing"
    NOT_SUPERUSER = "not_superuser"
    PROVIDER_UNWIRED = "provider_unwired"


@dataclass(frozen=True, slots=True)
class Denial:
    """One row of the outcome table: the error class and its message."""

    error: type[AppException]
    message: str


# The ONE outcome table. Every refusal in the principal path — the strict
# and optional bearer flows, the API-key flow, RBAC's gate, and the
# superuser gate — resolves its exception and message from here, so the
# nine entry points can no longer disagree about which status a given
# condition produces. Messages are byte-identical to what the entry points
# raised before the table existed.
DENIAL_TABLE: Mapping[DenialReason, Denial] = MappingProxyType(
    {
        DenialReason.NO_CREDENTIAL: Denial(UnauthorizedError, "User not authenticated."),
        DenialReason.INVALID_CREDENTIAL: Denial(UnauthorizedError, "User not authenticated."),
        DenialReason.INVALID_API_KEY: Denial(UnauthorizedError, "Invalid API key."),
        DenialReason.API_KEY_TENANT_MISMATCH: Denial(
            UnauthorizedError, "API key does not match the requested tenant context."
        ),
        DenialReason.REVOKED_TOKEN: Denial(UnauthorizedError, "Token has been revoked."),
        DenialReason.REVOKED_SESSION: Denial(UnauthorizedError, "Session has been revoked."),
        DenialReason.INACTIVE_USER: Denial(UnauthorizedError, "User account is no longer active."),
        # The superuser gate answers an inactive principal with 403, not 401:
        # reaching an admin-only endpoint is an authorization refusal. Kept as
        # its own row so the divergence is visible and named rather than
        # hidden in a second raise site.
        DenialReason.INACTIVE_SUPERUSER: Denial(ForbiddenError, "User account is no longer active."),
        DenialReason.DELETED_USER: Denial(UnauthorizedError, "User account has been deleted."),
        DenialReason.STALE_TOKEN_VERSION: Denial(UnauthorizedError, "Session expired. Please log in again."),
        DenialReason.TENANT_MISMATCH: Denial(ForbiddenError, "User does not belong to the current tenant."),
        DenialReason.TENANT_CONTEXT_MISSING: Denial(
            TenantContextError, "Tenant context required for permission check"
        ),
        DenialReason.SCOPE_UNVERIFIABLE: Denial(
            ForbiddenError, "API key scope cannot be verified for this request."
        ),
        DenialReason.SCOPE_MISSING: Denial(ForbiddenError, "API key missing required scope: {scope}"),
        DenialReason.NOT_SUPERUSER: Denial(ForbiddenError, "You do not have enough privileges."),
        DenialReason.PROVIDER_UNWIRED: Denial(
            ServiceUnavailableError, "Principal provider is not registered"
        ),
    }
)


def denial(reason: DenialReason, **fmt: Any) -> AppException:
    """Build the one canonical exception for *reason*.

    Unknown reasons fail loud (``KeyError``) rather than silently falling
    back to a default status — an unmapped refusal is a bug, not a policy.
    """
    spec = DENIAL_TABLE[reason]
    return spec.error(spec.message.format(**fmt) if fmt else spec.message)


class PrincipalProvider(Protocol):
    """Resolves the request principal without core importing the api layer (DIP).

    Implemented by ``api.deps.auth`` (which may legally import the module
    facades) and registered at import time; see
    :func:`register_principal_provider`.
    """

    async def __call__(
        self, request: Request, token: str | None = None
    ) -> AuthContext | None: ...


_principal_provider: PrincipalProvider | None = None


def register_principal_provider(provider: PrincipalProvider) -> None:
    """Register the principal-resolution provider (DIP seam)."""
    global _principal_provider
    _principal_provider = provider


def reset_principal_provider() -> None:
    """Reset the registered provider (test isolation seam).

    TEST-ONLY: guarded to non-production environments so production cannot
    be accidentally unwired into fail-closed 503s.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_principal_provider is test-only and blocked in production")
    global _principal_provider
    _principal_provider = None


def ensure_principal_provider_registered() -> None:
    """Assert the DIP seam is wired; call once at startup after imports.

    Raises :class:`ServiceUnavailableError` when the provider is missing so
    the app fails fast at boot instead of surfacing as mass 401s.
    """
    if _principal_provider is None:
        logger.error("principal_provider_unwired")
        raise denial(DenialReason.PROVIDER_UNWIRED)


async def get_auth_context(
    request: Request,
    token: str = Depends(oauth2_scheme),
) -> AuthContext:
    """Resolve the required principal, or refuse.

    The strict counterpart of ``get_optional_auth_context``: it is the ONE
    dependency RBAC and the strict endpoint shims consume. It is defined
    here rather than in ``api.deps.auth`` so ``core.rbac`` can depend on it
    without importing the api layer; ``api.deps.auth`` re-exports this
    exact object, which keeps ``app.dependency_overrides`` matching.

    Raises:
        AppException: The :data:`DENIAL_TABLE` row for the refusal cause.
    """
    provider = _principal_provider
    if provider is None:
        raise denial(DenialReason.PROVIDER_UNWIRED)
    ctx = await provider(request, token)

    if ctx is None:
        raise denial(DenialReason.NO_CREDENTIAL)
    return ctx


__all__ = [
    "DENIAL_TABLE",
    "OPTIONAL",
    "REQUIRED",
    "AuthenticatedUser",
    "AuthContext",
    "Denial",
    "DenialReason",
    "PrincipalPolicy",
    "PrincipalPresence",
    "PrincipalProvider",
    "denial",
    "ensure_principal_provider_registered",
    "get_auth_context",
    "register_principal_provider",
    "reset_principal_provider",
]
