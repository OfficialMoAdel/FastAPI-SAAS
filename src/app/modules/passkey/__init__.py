"""Passkey (WebAuthn) authentication module.

Provides FIDO2/WebAuthn authentication via passkeys.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.passkey.models import PasskeyCredential
    from src.app.modules.passkey.service import PasskeyService
    from src.app.modules.passkey.router import router as passkey_router
"""

__all__ = [
    "PasskeyCredential",
    "PasskeyService",
    "router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from src.app.modules.passkey.router import router

        return router
    if name == "PasskeyService":
        from src.app.modules.passkey.service import PasskeyService

        return PasskeyService
    if name == "PasskeyCredential":
        from src.app.modules.passkey.models import PasskeyCredential

        return PasskeyCredential
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
