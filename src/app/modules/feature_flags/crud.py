"""CRUD operations for feature flags and overrides."""

from typing import Any

from fastcrud import FastCRUD

from src.app.core.db.tenant_crud import TenantAwareCRUD
from src.app.modules.feature_flags.models import FeatureFlag, FeatureFlagOverride
from src.app.modules.feature_flags.schemas import (
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideRead,
    FeatureFlagOverrideUpdate,
    FeatureFlagRead,
    FeatureFlagUpdate,
)

CRUDFeatureFlag = FastCRUD[
    FeatureFlag,
    FeatureFlagCreate,
    FeatureFlagUpdate,
    FeatureFlagUpdate,
    Any,
    FeatureFlagRead,
]
crud_feature_flags = CRUDFeatureFlag(FeatureFlag)

CRUDFeatureFlagOverride = TenantAwareCRUD[
    FeatureFlagOverride,
    FeatureFlagOverrideCreate,
    FeatureFlagOverrideUpdate,
    FeatureFlagOverrideUpdate,
    Any,
    FeatureFlagOverrideRead,
]
crud_feature_flag_overrides = CRUDFeatureFlagOverride(FeatureFlagOverride)
