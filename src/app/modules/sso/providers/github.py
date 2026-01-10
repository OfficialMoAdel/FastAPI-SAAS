from typing import Any

import httpx

from src.app.modules.sso.providers.base import BaseOAuthProvider


class GitHubProvider(BaseOAuthProvider):
    """GitHub OAuth2 Provider implementation."""

    @property
    def provider_name(self) -> str:
        return "github"

    @property
    def authorization_endpoint(self) -> str:
        return "https://github.com/login/oauth/authorize"

    @property
    def token_endpoint(self) -> str:
        return "https://github.com/login/oauth/access_token"

    @property
    def userinfo_endpoint(self) -> str:
        return "https://api.github.com/user"

    def get_default_scopes(self) -> list[str]:
        return ["read:user", "user:email"]

    @property
    def is_oidc_compliant(self) -> bool:
        return False

    def requires_verified_email(self) -> bool:
        return True

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        # GitHub requires Accept header for JSON response
        response_data = await self._http_post(
            self.token_endpoint,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        return response_data

    async def get_user_info(self, access_token: str) -> Any:
        """Get user info from GitHub."""
        # GitHub returns user email only with user:email scope, and it's a separate endpoint
        # Get basic user info first
        data = await self._http_get(
            self.userinfo_endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        # Try to get user emails (requires user:email scope)
        email_verified = False
        try:
            emails_data = await self._http_get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            # Find primary verified email
            primary_email = None
            for email_obj in emails_data:
                if email_obj.get("primary", False) and email_obj.get("verified", False):
                    primary_email = email_obj.get("email")
                    email_verified = True
                    break
            if primary_email:
                data["email"] = primary_email
            data["email_verified"] = email_verified
        except Exception:
            # Email endpoint failed, use None
            data["email"] = None
            data["email_verified"] = False

        # Normalize to internal structure
        class UserInfo:
            def __init__(self, data):
                self.provider_user_id = str(data.get("id"))
                self.email = data.get("email")
                self.email_verified = data.get("email_verified")
                self.name = data.get("name") or data.get("login")  # Use login if name is None
                self.picture = data.get("avatar_url")

        return UserInfo(data)

    # Helper methods for mocking in tests
    async def _http_post(self, url: str, data: dict[str, Any], headers: dict[str, str] = None) -> dict[str, Any]:
        """Wrapper for HTTP POST to allow easy mocking."""
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=data, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _http_get(self, url: str, headers: dict[str, str] = None) -> dict[str, Any]:
        """Wrapper for HTTP GET to allow easy mocking."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
