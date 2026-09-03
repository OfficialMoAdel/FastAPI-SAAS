"""Auth module - unified authentication, identity, and authorization.

Consolidated deep module providing:
- Core login/logout, token refresh, blacklisting
- Password lifecycle and email verification
- OAuth/OIDC Single Sign-On (SSO) and tenant provider settings
- WebAuthn passkey registration and assertion
- Magic link passwordless authentication
- Multi-factor authentication (TOTP and backup codes)
"""

from __future__ import annotations

from typing import Any

__all__ = [
    # Routers
    "auth_router",
    "sso_router",
    "sso_settings_router",
    "passkey_router",
    "magic_link_router",
    "mfa_router",
    # Services
    "AuthService",
    "AuthGateway",
    "get_auth_service",
    "get_auth",
    "SSOService",
    "get_sso_service",
    "SSOSettingsService",
    "sso_settings_service",
    "PasskeyService",
    "get_passkey_service",
    "passkey_service",
    "MagicLinkService",
    "get_magic_link_service",
    "MFAService",
    "get_mfa_service",
    # Models
    "PasswordResetToken",
    "EmailVerificationToken",
    "SSOSettings",
    "UserSSOConnection",
    "PasskeyCredential",
    "MagicLink",
    "MFAMethod",
]


def __getattr__(name: str) -> Any:
    """Lazy import to avoid circular imports."""
    if name in (
        "auth_router",
        "sso_router",
        "sso_settings_router",
        "passkey_router",
        "magic_link_router",
        "mfa_router",
    ):
        from src.app.modules.auth import router as r

        return getattr(r, name)

    if name in ("AuthService", "AuthGateway", "get_auth_service", "get_auth"):
        from src.app.modules.auth import service as s

        return getattr(s, name)

    if name in ("SSOService", "get_sso_service"):
        from src.app.modules.auth.services.sso import SSOService, get_sso_service

        return SSOService if name == "SSOService" else get_sso_service

    if name in ("SSOSettingsService", "sso_settings_service"):
        from src.app.modules.auth.services.sso_settings import SSOSettingsService, sso_settings_service

        return SSOSettingsService if name == "SSOSettingsService" else sso_settings_service

    if name in ("PasskeyService", "get_passkey_service", "passkey_service"):
        from src.app.modules.auth.services.passkey import PasskeyService, get_passkey_service, passkey_service

        return (
            PasskeyService
            if name == "PasskeyService"
            else get_passkey_service
            if name == "get_passkey_service"
            else passkey_service
        )

    if name in ("MagicLinkService", "get_magic_link_service"):
        from src.app.modules.auth.services.magic_link import MagicLinkService, get_magic_link_service

        return MagicLinkService if name == "MagicLinkService" else get_magic_link_service

    if name in ("MFAService", "get_mfa_service"):
        from src.app.modules.auth.services.mfa import MFAService, get_mfa_service

        return MFAService if name == "MFAService" else get_mfa_service

    if name in (
        "PasswordResetToken",
        "EmailVerificationToken",
        "SSOSettings",
        "UserSSOConnection",
        "PasskeyCredential",
        "MagicLink",
        "MFAMethod",
    ):
        from src.app.modules.auth import models as m

        return getattr(m, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
