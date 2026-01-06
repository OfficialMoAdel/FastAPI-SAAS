from typing import Any, Dict

import httpx

from src.app.modules.sso.providers.base import BaseOAuthProvider


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

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
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

    async def get_user_info(self, access_token: str) -> Any:
        """Get user info from Microsoft Graph API."""
        data = await self._http_get(
            self.userinfo_endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )

        # Normalize to internal structure
        class UserInfo:
            def __init__(self, data):
                # Microsoft uses id (UUID) instead of id (numeric)
                self.provider_user_id = str(data.get("id"))
                self.email = data.get("mail") or data.get("userPrincipalName")
                # Microsoft emails are typically verified
                self.email_verified = data.get("emailVerified", True)
                self.name = data.get("displayName") or data.get("givenName", "")
                # Photo requires additional API call, keeping it None for now
                self.picture = None

        return UserInfo(data)

    # Helper methods for mocking in tests
    async def _http_post(self, url: str, data: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
        """Wrapper for HTTP POST to allow easy mocking."""
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _http_get(self, url: str, headers: Dict[str, str] = None) -> Dict[str, Any]:
        """Wrapper for HTTP GET to allow easy mocking."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
