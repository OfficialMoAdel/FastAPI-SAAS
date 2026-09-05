"""Feature Flags module."""

from src.app.modules.feature_flags.crud import (
    CRUDFeatureFlag,
    CRUDFeatureFlagOverride,
    crud_feature_flag_overrides,
    crud_feature_flags,
)
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.schemas import (
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
from src.app.modules.feature_flags.service import (
    FeatureFlagService,
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
    # CRUD
    "CRUDFeatureFlag",
    "CRUDFeatureFlagOverride",
    "crud_feature_flags",
    "crud_feature_flag_overrides",
]
