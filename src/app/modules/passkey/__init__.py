"""Passkey (WebAuthn) authentication module.

Provides FIDO2/WebAuthn authentication via passkeys.

Exports:
    - PasskeyCredential: SQLAlchemy model for passkey credentials
    - PasskeyService: Service layer for passkey operations
    - router: FastAPI router with passkey endpoints
"""

from src.app.modules.passkey.router import router

__all__ = ["router"]
