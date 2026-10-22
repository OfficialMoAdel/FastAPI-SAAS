"""API Key Pydantic schemas."""

import json
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.app.modules._base.schemas import BaseCreate, BaseList, BaseRead


class APIKeyBase(BaseModel):
    """Base schema for API key."""

    name: Annotated[str, Field(min_length=1, max_length=100, examples=["Production API Key"])]
    # FastAPI_saas-ns82: the default was `None`, and `create_api_key` only
    # persists a truthy value, so omitting `scopes` stored NULL - which
    # `api_key_scope_allows` denies everywhere by design ("Keys with no scopes
    # are denied everywhere"). The endpoint's own default therefore minted a
    # key that could call nothing at all: M-7 measured exactly that, a 403
    # "API key missing required scope: read" on its first GET
    # (perf-results/RESULT-20261017-m7/m7_apikey_endpoint_baseline.json).
    #
    # The default is now the LEAST privilege that makes a key usable, not the
    # `admin` wildcard and not `None`. Explicitly supplied values are never
    # touched: `"scopes": []` or `"scopes": null` still yields a key that is
    # denied everywhere, because that is a statement rather than an omission,
    # and the fail-closed policy is pinned by tests/unit/api/deps/test_auth.py.
    scopes: list[str] | None = Field(
        default_factory=lambda: ["read"],
        examples=[["read:posts", "write:posts"]],
    )
    expires_at: datetime | None = Field(default=None, description="Optional expiration date")


class APIKeyCreate(APIKeyBase, BaseCreate):
    """Schema for creating an API key.

    `user_id` names the account the key is minted FOR. It is optional, and
    omitted or equal to the caller means exactly what it always meant: a key for
    yourself. Without it a tenant admin had to impersonate a user to hand them a
    credential (found by M-7, FastAPI_saas-aepv).

    The field is only a CLAIM. It is honoured only when it differs from the
    caller, and `APIKeyService._resolve_key_owner` then verifies the target
    exists in the caller's tenant, is not soft-deleted and is active - so a
    target in another tenant is indistinguishable from one that does not exist,
    rather than an oracle for which user ids are real. The gate is the route's
    existing `users:manage` requirement, unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int | None = Field(
        default=None,
        ge=1,
        description="Account the key is issued for. Omit (or pass your own id) to mint for yourself.",
    )


class APIKeyRead(BaseRead):
    """Schema for reading an API key (without the actual key)."""

    name: str
    key_prefix: str
    user_id: int
    tenant_id: UUID
    is_active: bool
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    @field_validator("scopes", mode="before")
    @classmethod
    def parse_scopes(cls, v: Any) -> list[str] | None:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
                return None
            except json.JSONDecodeError:
                return [s.strip() for s in v.split(",") if s.strip()]

        if isinstance(v, list):
            return [str(item) for item in v]
        return None

    model_config = ConfigDict(from_attributes=True)


class APIKeyResponse(BaseModel):
    """Schema for API key creation response (includes the actual key - only shown once)."""

    id: int
    name: str
    key: str  # The full API key - only returned on creation
    key_prefix: str
    expires_at: datetime | None
    created_at: datetime
    message: str = "Store this key securely. It will not be shown again."


class APIKeyList(BaseList[APIKeyRead]):
    """Standardized paginated API key list response.

    Inherits from BaseList: items, total, page, per_page, has_next, has_previous
    """

    pass


class APIKeyListResponse(BaseModel):
    """Schema for listing API keys.

    Deprecated: Use APIKeyList for new endpoints.
    This schema is kept for backward compatibility with existing API contracts.
    """

    data: list[APIKeyRead]
    total_count: int


class APIKeyRevokeResponse(BaseModel):
    """Schema for API key revocation response."""

    message: str = "API key revoked successfully"


class APIKeyRotateResponse(APIKeyResponse):
    """Schema for API key rotation response.

    Extends APIKeyResponse to include grace period info.

    `valid_from` is the NEW key's own start-of-validity timestamp, which is
    `None` because a rotated key is valid immediately - the grace window
    belongs to the OLD key's expiry, not to the new key's activation.

    `grace_period_days` is the overlap window that was actually applied, not
    the one that was requested: a request above `MAX_GRACE_PERIOD_DAYS` is
    clamped, and reporting the requested value would misstate the window the
    old key actually got. The old key's own earlier expiry can shorten the
    window further, independently of this value.
    """

    valid_from: datetime | None = Field(
        default=None,
        description="When the new key becomes valid (null: it is valid immediately).",
    )
    grace_period_days: int = Field(
        default=7,
        ge=0,
        description="Overlap window in days granted to the old key (clamped to the maximum).",
    )
    message: str = (
        "Key rotated successfully. Old key remains valid for grace period. "
        "Store new key securely - it will not be shown again."
    )
