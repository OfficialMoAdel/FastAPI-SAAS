from fastcrud import FastCRUD

from .models import SSOSettings
from .schemas import (
    SSOSettingsCreateInternal,
    SSOSettingsRead,
    SSOSettingsUpdateInternal,
)


class CRUDSSOSettings(
    FastCRUD[
        SSOSettings,
        SSOSettingsCreateInternal,
        SSOSettingsUpdateInternal,
        SSOSettingsUpdateInternal,
        dict,
        SSOSettingsRead,
    ]
):
    pass


crud_sso_settings = CRUDSSOSettings(SSOSettings)
