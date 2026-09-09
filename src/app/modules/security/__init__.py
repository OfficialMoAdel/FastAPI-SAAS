"""
Security Module (A-M7 naming-collision note)

NOT the same package as ``src/app/core/security/``. This module provides
convenience *HTTP endpoints* for passkey operations (backward-compat wrapper
for the old backend's /api/v1/security/* routes); ``core/security/`` is the
core-library cryptography surface (JWT, password hashing, blacklist caches).
The name collision is deliberate legacy — renames break imports; distinguish
by layer: this = routers, core/security = primitives.

Provides convenience endpoints for security-related operations,
particularly passkey management.
"""

from src.app.modules.security.router import router

__all__ = ["router"]
