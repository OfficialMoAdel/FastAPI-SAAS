"""Pydantic schemas for feature flags."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from .._base import BaseCreate, BaseList, BaseRead, BaseUpdate

# =============================================================================
# Response Schemas (API-facing)
# =============================================================================


class FeatureFlagsResponse(BaseModel):
    """Schema for all feature flags response."""

    feature_flags: dict[str, Any] = Field(default_factory=dict, description="All feature flags for tenant")


class FeatureFlagCheckResponse(BaseModel):
    """Schema for single feature flag check response."""

    flag_name: str
    enabled: bool
    value: Any | None = None


# =============================================================================
# Request Schemas (API-facing)
# =============================================================================


class FeatureFlagUpdateRequest(BaseModel):
    """Schema for updating a feature flag."""

    enabled: bool | None = Field(None, description="Enable or disable the feature")
    value: Any | None = Field(None, description="Optional value for the feature (for non-boolean flags)")


class FeatureFlagBulkUpdateRequest(BaseModel):
    """Schema for bulk updating feature flags."""

    feature_flags: dict[str, Any] = Field(..., description="Complete feature flags object to set")


# =============================================================================
# CRUD Schemas (for BaseService operations)
# =============================================================================


class FeatureFlagBase(BaseModel):
    """Base schema for FeatureFlag with common fields."""

    key: str = Field(..., min_length=1, max_length=255, description="Unique flag key")
    description: str | None = Field(None, max_length=500, description="Flag description")
    default_value: bool = Field(True, description="Default enabled state")
    value_type: str = Field("boolean", description="Type of flag value")
    flag_metadata: dict[str, Any] | None = Field(None, description="Additional metadata")
    is_system: bool = Field(False, description="Whether this is a system flag")


class FeatureFlagCreate(FeatureFlagBase, BaseCreate):
    """Schema for creating a feature flag."""

    pass


class FeatureFlagUpdate(BaseUpdate):
    """Schema for updating a feature flag."""

    key: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    default_value: bool | None = None
    value_type: str | None = None
    flag_metadata: dict[str, Any] | None = None
    is_system: bool | None = None


class FeatureFlagRead(BaseRead):
    """Schema for reading a feature flag."""

    uuid: UUID
    key: str
    description: str | None
    default_value: bool
    value_type: str
    flag_metadata: dict[str, Any] | None
    is_system: bool


class FeatureFlagList(BaseList[FeatureFlagRead]):
    """Paginated list of feature flags."""

    pass


# =============================================================================
# Override Schemas
# =============================================================================


class FeatureFlagOverrideBase(BaseModel):
    """Base schema for FeatureFlagOverride."""

    feature_flag_id: int = Field(..., description="ID of the feature flag")
    custom_value: dict[str, Any] | None = Field(None, description="Custom override value")
    reason: str | None = Field(None, max_length=500, description="Reason for override")


class FeatureFlagOverrideCreate(FeatureFlagOverrideBase, BaseCreate):
    """Schema for creating a feature flag override."""

    tenant_id: UUID = Field(..., description="Tenant ID for the override")


class FeatureFlagOverrideUpdate(BaseUpdate):
    """Schema for updating a feature flag override."""

    custom_value: dict[str, Any] | None = None
    reason: str | None = None


class FeatureFlagOverrideRead(BaseRead):
    """Schema for reading a feature flag override."""

    uuid: UUID
    tenant_id: UUID
    feature_flag_id: int
    custom_value: dict[str, Any] | None
    reason: str | None
