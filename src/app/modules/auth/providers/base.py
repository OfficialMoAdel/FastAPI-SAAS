from abc import ABC, abstractmethod
from typing import Any

from src.app.modules.auth.http_client import sso_http_get, sso_http_post


class BaseOAuthProvider(ABC):
    """Abstract base class for OAuth2 providers.

    Uses shared httpx.AsyncClient for connection pooling and performance.
    Subclasses should use _http_post() and _http_get() helper methods.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: list[str] | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scope or self.get_default_scopes()

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the unique name of the provider (e.g., 'google')."""
        pass

    @property
    @abstractmethod
    def authorization_endpoint(self) -> str:
        """Return the authorization URL for the provider."""
        pass

    @property
    @abstractmethod
    def token_endpoint(self) -> str:
        """Return the token exchange URL for the provider."""
        pass

    @property
    @abstractmethod
    def userinfo_endpoint(self) -> str:
        """Return the user info URL for the provider."""
        pass

    @abstractmethod
    def get_default_scopes(self) -> list[str]:
        """Return the default scopes for the provider."""
        pass

    def get_authorization_url(self, state: str) -> str:
        """Generate the authorization URL."""
        # This is a simplified implementation for the test
        # In a real implementation, this would use an OAuth library or construct the URL manually
        # checking the tests to see what they expect
        import urllib.parse

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
        }
        return f"{self.authorization_endpoint}?{urllib.parse.urlencode(params)}"

    async def _http_post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP POST request using shared connection pool.

        Args:
            url: Target URL for the POST request
            data: Form data to send
            headers: Optional headers to include

        Returns:
            JSON response as dictionary
        """
        return await sso_http_post(url, data=data or {}, headers=headers)

    async def _http_get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make HTTP GET request using shared connection pool.

        Args:
            url: Target URL for the GET request
            headers: Optional headers to include

        Returns:
            JSON response as dictionary
        """
        return await sso_http_get(url, headers=headers)

    @abstractmethod
    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange the authorization code for an access token."""
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str, id_token: str | None = None) -> Any:
        """Retrieve user information using the access token.

        ``id_token`` is optional: OIDC-compliant providers (e.g. Microsoft)
        may receive it so claims like ``email_verified`` can be read from its
        payload. Providers that don't need it ignore the parameter.

        Returns the provider's user-info object (shape varies by provider).
        """
        pass
