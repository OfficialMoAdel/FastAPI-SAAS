"""SSO (Single Sign-On) authentication module.

Provides OAuth2 authentication via external identity providers.

Exports:
    - UserSSOConnection: SQLAlchemy model for SSO connections
    - SSOService: Service layer for SSO operations
    - router: FastAPI router with SSO endpoints
"""

from src.app.modules.sso.router import router

__all__ = ["router"]
