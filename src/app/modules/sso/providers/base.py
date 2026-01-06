from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseOAuthProvider(ABC):
    """Abstract base class for OAuth2 providers."""

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

    @abstractmethod
    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange the authorization code for an access token."""
        pass

    @abstractmethod
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Retrieve user information using the access token."""
        pass
