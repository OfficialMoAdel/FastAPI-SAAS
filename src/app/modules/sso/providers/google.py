from typing import Any, Dict

import httpx

from src.app.modules.sso.providers.base import BaseOAuthProvider


class GoogleProvider(BaseOAuthProvider):
    """Google OAuth2 Provider implementation."""

    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def authorization_endpoint(self) -> str:
        return "https://accounts.google.com/o/oauth2/v2/auth"

    @property
    def token_endpoint(self) -> str:
        return "https://oauth2.googleapis.com/token"

    @property
    def userinfo_endpoint(self) -> str:
        return "https://www.googleapis.com/oauth2/v3/userinfo"

    def get_default_scopes(self) -> list[str]:
        return ["openid", "email", "profile"]

    @property
    def is_oidc_compliant(self) -> bool:
        return True

    def requires_verified_email(self) -> bool:
        return True

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token."""
        return await self._http_post(
            self.token_endpoint,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )

    async def get_user_info(self, access_token: str) -> Any:
        """Get user info from Google."""
        data = await self._http_get(
            self.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        # Normalize to internal structure (using a simple object for now as per test expectation)
        class UserInfo:
            def __init__(self, data):
                self.provider_user_id = data.get("sub")
                self.email = data.get("email")
                self.email_verified = data.get("email_verified")
                self.name = data.get("name")
                self.picture = data.get("picture")

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
