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

    def requires_verified_email(self) -> bool:
        """Whether this provider guarantees a verified email.

        True (the default) means an explicit verification verdict is
        required before a new account may be created from this provider's
        identity; the SSO gate rejects a verdict of False. Providers that
        cannot make such a guarantee override this to False.
        """
        return True

    def trusts_identity_without_claim(self) -> bool:
        """Whether an ABSENT email-verification claim may be treated as trusted.

        The first-time-signup gate distinguishes three verdicts: asserted
        verified (True), asserted unverified (False), and unknown (None —
        claim absent, or the id_token could not be verified). For most
        providers "unknown" must fail closed: Google and GitHub emit an
        explicit boolean, so its absence means "not verified".

        Directory-authority providers (Microsoft Entra, Apple) guarantee the
        mailbox namespace structurally — the tenant owns the UPN/mailbox, and
        Apple relay addresses are verified at issuance. Those providers
        override this to True so a missing claim does not block signup.
        """
        return False

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
