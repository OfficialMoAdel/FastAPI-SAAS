"""Feature Flags module."""

from .models import FeatureFlag, FeatureFlagOverride
from .schemas import (
    FeatureFlagCheckResponse,
    FeatureFlagCreate,
    FeatureFlagList,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideRead,
    FeatureFlagOverrideUpdate,
    FeatureFlagRead,
    FeatureFlagsResponse,
    FeatureFlagUpdate,
)
from .service import (
    FeatureFlagService,
    feature_flags_service,  # Legacy compatibility
    get_feature_flag_service,
)

__all__ = [
    # Models
    "FeatureFlag",
    "FeatureFlagOverride",
    # Schemas
    "FeatureFlagCreate",
    "FeatureFlagRead",
    "FeatureFlagUpdate",
    "FeatureFlagList",
    "FeatureFlagsResponse",
    "FeatureFlagCheckResponse",
    "FeatureFlagOverrideCreate",
    "FeatureFlagOverrideRead",
    "FeatureFlagOverrideUpdate",
    # Service
    "FeatureFlagService",
    "get_feature_flag_service",
    "feature_flags_service",  # Legacy
]
