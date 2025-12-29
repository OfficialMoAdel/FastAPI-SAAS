"""Pydantic schemas for feature flags."""

from typing import Any

from pydantic import BaseModel, Field


class FeatureFlagsResponse(BaseModel):
    """Schema for all feature flags response."""

    feature_flags: dict[str, Any] = Field(default_factory=dict, description="All feature flags for tenant")


class FeatureFlagCheckResponse(BaseModel):
    """Schema for single feature flag check response."""

    flag_name: str
    enabled: bool
    value: Any | None = None


class FeatureFlagUpdateRequest(BaseModel):
    """Schema for updating a feature flag."""

    enabled: bool | None = Field(None, description="Enable or disable the feature")
    value: Any | None = Field(None, description="Optional value for the feature (for non-boolean flags)")


class FeatureFlagBulkUpdateRequest(BaseModel):
    """Schema for bulk updating feature flags."""

    feature_flags: dict[str, Any] = Field(..., description="Complete feature flags object to set")
