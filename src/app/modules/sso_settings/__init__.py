"""SSO Settings administration module.

Provides tenant-level SSO provider configuration management.

Exports:
    - SSOSettings: SQLAlchemy model for SSO provider settings
    - SSOSettingsService: Service layer for SSO settings operations
    - router: FastAPI router with SSO settings endpoints
"""

__all__ = ["router"]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from .router import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
