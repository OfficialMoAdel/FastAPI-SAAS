from fastcrud import FastCRUD

from .models import SSOSettings
from .schemas import (
    SSOSettingsCreateInternal,
    SSOSettingsDelete,
    SSOSettingsRead,
    SSOSettingsUpdateInternal,
)


class CRUDSSOSettings(
    FastCRUD[
        SSOSettings,
        SSOSettingsCreateInternal,
        SSOSettingsUpdateInternal,
        SSOSettingsUpdateInternal,
        SSOSettingsDelete,
        SSOSettingsRead,
    ]
):
    pass


crud_sso_settings = CRUDSSOSettings(SSOSettings)
