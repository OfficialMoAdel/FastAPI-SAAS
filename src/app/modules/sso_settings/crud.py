from fastcrud import FastCRUD

from src.app.modules.sso_settings.models import SSOSettings
from src.app.modules.sso_settings.schemas import (
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
