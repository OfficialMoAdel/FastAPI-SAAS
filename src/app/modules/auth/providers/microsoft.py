import asyncio
import time
from typing import Any

import jwt

from src.app.core.logger import get_logger
from src.app.modules.auth.providers.base import BaseOAuthProvider

logger = get_logger(__name__)

# P1-4: see apple.py. One cached client per module; ``lifespan`` bounds the
# key cache so a rotated key is still picked up.
_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
_JWKS_CACHE_SECONDS = 300
_jwks_client = jwt.PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS)


class MicrosoftProvider(BaseOAuthProvider):
    """Microsoft Azure AD OAuth2 Provider implementation."""

    @property
    def provider_name(self) -> str:
        return "microsoft"

    @property
    def authorization_endpoint(self) -> str:
        # Common endpoint for all Microsoft tenants
        return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:
        return "https://login.microsoftonline.com/common/oauth2/v2.0/token"

    @property
    def userinfo_endpoint(self) -> str:
        # Microsoft Graph API endpoint
        return "https://graph.microsoft.com/v1.0/me"

    def get_default_scopes(self) -> list[str]:
        # Default scopes for Microsoft Graph
        return ["openid", "email", "profile"]

    @property
    def is_oidc_compliant(self) -> bool:
        return True

    def requires_verified_email(self) -> bool:
        # Microsoft typically requires verified emails
        return True

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        response_data = await self._http_post(
            self.token_endpoint,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(self.scopes) if self.scopes else " ".join(self.get_default_scopes()),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return response_data

    async def get_user_info(self, access_token: str, id_token: str | None = None) -> Any:
        """Get user info from Microsoft Graph API.

        M10: accepts the OIDC id_token so `email_verified` can be read from
        its claims. Microsoft may omit that claim — default False (fail
        closed) unless explicitly true.
        """
        data = await self._http_get(
            self.userinfo_endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        email_verified = False
        if id_token:
            try:
                claims = await self._verify_id_token(id_token)
                email_verified = claims.get("email_verified") is True
            except Exception:
                email_verified = False

        # Normalize to internal structure
        class UserInfo:
            def __init__(self, data, email_verified):
                # Microsoft uses id (UUID) instead of id (numeric)
                self.provider_user_id = str(data.get("id"))
                self.email = data.get("mail") or data.get("userPrincipalName")
                self.email_verified = email_verified
                self.name = data.get("displayName") or data.get("givenName", "")
                # Photo requires additional API call, keeping it None for now
                self.picture = None

        return UserInfo(data, email_verified)

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify and decode Microsoft's OIDC id_token JWT.

        Mirrors AppleProvider._verify_id_token: signature checked against
        the Microsoft JWKS, audience pinned to our client_id, issuer
        pinned to login.microsoftonline.com v2.0. Fail-closed: any
        verification error raises, and the caller treats the token as
        unverified (email_verified=False).
        """
        # P1-4: get_signing_key_from_jwt performs a SYNCHRONOUS urllib HTTPS
        # GET. Called directly from async def it blocks the event loop, so it
        # is offloaded to a worker thread. See apple.py for the full rationale.
        started = time.monotonic()
        signing_key = await asyncio.to_thread(_jwks_client.get_signing_key_from_jwt, id_token)
        logger.info(
            "microsoft_jwks_signing_key_resolved",
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )

        claims: dict[str, Any] = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.client_id,
            options={"verify_iss": False},
        )
        issuer = str(claims.get("iss", ""))
        if not (
            issuer.startswith("https://login.microsoftonline.com/") and issuer.endswith("/v2.0")
        ):
            raise ValueError(f"Unexpected id_token issuer: {issuer}")
        return claims
