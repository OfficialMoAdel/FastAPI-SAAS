"""Security package exports.

This module exports authentication, password hashing, JWT token management,
and blacklisting utilities from specialized submodules to maintain clean architecture
while providing a convenient public API.
"""

from __future__ import annotations

from src.app.core.security.jwt_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    MFA_PENDING_EXPIRY_SECONDS,
    TokenType,
    _check_db_fallback,
    _resolve_tenant_id_for_token,
    api_key_scheme,
    blacklist_token,
    blacklist_tokens,
    create_access_token,
    create_refresh_token,
    get_mfa_pending_token_manager,
    oauth2_scheme,
    verify_token,
)
from src.app.core.security.password import (
    _bcrypt_executor,
    _get_bcrypt_executor,
    get_password_hash,
    verify_password,
)
from src.app.core.security.token_blacklist_cache import (
    add_token_to_blacklist,
    add_tokens_to_blacklist,
    is_token_blacklisted,
)
from src.app.core.security.tokens import TokenGenerator

__all__ = [
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "MFA_PENDING_EXPIRY_SECONDS",
    "TokenGenerator",
    "TokenType",
    "_bcrypt_executor",
    "_check_db_fallback",
    "_get_bcrypt_executor",
    "_resolve_tenant_id_for_token",
    "add_token_to_blacklist",
    "add_tokens_to_blacklist",
    "api_key_scheme",
    "blacklist_token",
    "blacklist_tokens",
    "create_access_token",
    "create_refresh_token",
    "get_mfa_pending_token_manager",
    "get_password_hash",
    "is_token_blacklisted",
    "oauth2_scheme",
    "verify_password",
    "verify_token",
]
