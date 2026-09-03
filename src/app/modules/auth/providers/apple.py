"""Apple OAuth2 Provider implementation.

Apple Sign-In specifics:
- Uses POST callback (response_mode=form_post) instead of GET
- Private email relay support (privaterelay.appleid.com)
- Scopes: name email
- Uses JWT id_token for user info (no separate userinfo endpoint)
- Client secret is a signed JWT (not static string)
- Team ID, Key ID, and Private Key required for client secret generation
"""

import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.app.modules.auth.providers.base import BaseOAuthProvider


class AppleUserInfo:
    """Normalized user info from Apple Sign-In."""

    def __init__(
        self,
        provider_user_id: str,
        email: str | None = None,
        email_verified: bool = False,
        is_private_email: bool = False,
        name: str | None = None,
        picture: str | None = None,
    ):
        self.provider_user_id = provider_user_id
        self.email = email
        self.email_verified = email_verified
        self.is_private_email = is_private_email
        self.name = name
        self.picture = picture  # Apple doesn't provide profile pictures


class AppleProvider(BaseOAuthProvider):
    """Apple Sign-In OAuth2 Provider implementation.

    Apple has several unique requirements:
    1. Uses response_mode=form_post (POST callback instead of GET)
    2. Client secret is a JWT signed with a private key
    3. User info is embedded in id_token, no userinfo endpoint
    4. User name is only provided on first authorization (in POST body)
    5. Supports private email relay for user privacy
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,  # Not used directly, we generate JWT
        redirect_uri: str,
        team_id: str,
        key_id: str,
        private_key: str,
        scope: list[str] | None = None,
    ):
        """Initialize Apple provider with required credentials.

        Args:
            client_id: Apple Service ID (e.g., com.example.app)
            client_secret: Placeholder (actual secret is generated JWT)
            redirect_uri: OAuth callback URL
            team_id: Apple Developer Team ID
            key_id: Key ID for the private key
            private_key: P-256 private key in PEM format
            scope: Optional list of scopes (defaults to name, email)
        """
        self.team_id = team_id
        self.key_id = key_id
        self.private_key = private_key
        super().__init__(client_id, client_secret, redirect_uri, scope)

    @property
    def provider_name(self) -> str:
        return "apple"

    @property
    def authorization_endpoint(self) -> str:
        return "https://appleid.apple.com/auth/authorize"

    @property
    def token_endpoint(self) -> str:
        return "https://appleid.apple.com/auth/token"

    @property
    def userinfo_endpoint(self) -> str | None:  # type: ignore[override]
        """Apple doesn't have a userinfo endpoint - uses id_token."""
        return None

    def get_default_scopes(self) -> list[str]:
        return ["name", "email"]

    @property
    def is_oidc_compliant(self) -> bool:
        """Apple is OIDC compliant."""
        return True

    @property
    def uses_post_callback(self) -> bool:
        """Apple uses POST for OAuth callback."""
        return True

    @property
    def supports_private_email_relay(self) -> bool:
        """Apple supports private email relay for user privacy."""
        return True

    def requires_verified_email(self) -> bool:
        """Apple always provides verified emails."""
        return True

    def get_authorization_url(self, state: str) -> str:
        """Generate Apple authorization URL with form_post response mode."""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "response_mode": "form_post",  # Apple-specific: POST callback
        }
        return f"{self.authorization_endpoint}?{urllib.parse.urlencode(params)}"

    def _generate_client_secret(self) -> str:
        """Generate a signed JWT to use as client secret.

        Apple requires the client secret to be a JWT signed with
        the private key associated with the Sign-In with Apple key.

        The JWT must contain:
        - iss: Team ID
        - sub: Client ID (Service ID)
        - aud: https://appleid.apple.com
        - iat: Issue time
        - exp: Expiration time (max 6 months)
        """
        now = datetime.now(UTC)
        expiry = now + timedelta(days=180)  # Max 6 months

        headers = {
            "alg": "ES256",
            "kid": self.key_id,
        }

        payload = {
            "iss": self.team_id,
            "sub": self.client_id,
            "aud": "https://appleid.apple.com",
            "iat": int(now.timestamp()),
            "exp": int(expiry.timestamp()),
        }

        return jwt.encode(payload, self.private_key, algorithm="ES256", headers=headers)

    async def exchange_code_for_token(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for tokens.

        Apple returns:
        - access_token
        - id_token (JWT containing user info)
        - refresh_token
        - expires_in
        """
        client_secret = self._generate_client_secret()

        return await self._http_post(
            self.token_endpoint,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    async def get_user_info(self, id_token: str, user_data: dict[str, Any] | None = None) -> AppleUserInfo:  # type: ignore[override]
        """Extract user info from Apple's id_token JWT.

        Unlike other providers, Apple embeds user info in the id_token.
        The user's name is only provided on the first authorization and
        comes in the POST body, not the id_token.

        Args:
            id_token: JWT id_token from token exchange
            user_data: Optional user data from POST body (first auth only)

        Returns:
            AppleUserInfo with normalized user data
        """
        # Verify and decode the id_token
        claims = await self._verify_id_token(id_token)

        # Extract name from POST body user data (first auth only)
        name = None
        if user_data and "name" in user_data:
            name_data = user_data["name"]
            first_name = name_data.get("firstName", "")
            last_name = name_data.get("lastName", "")
            name = f"{first_name} {last_name}".strip() or None

        return AppleUserInfo(
            provider_user_id=claims["sub"],
            email=claims.get("email"),
            email_verified=claims.get("email_verified", False),
            is_private_email=claims.get("is_private_email", False),
            name=name,
        )

    async def _verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Verify and decode Apple's id_token JWT.

        In production, this should verify:
        1. Signature using Apple's public keys
        2. Issuer is https://appleid.apple.com
        3. Audience matches our client_id
        4. Token is not expired
        """
        try:
            jwks_client = jwt.PyJWKClient("https://appleid.apple.com/auth/keys")
            signing_key = jwks_client.get_signing_key_from_jwt(id_token)

            claims: dict[str, Any] = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer="https://appleid.apple.com",
            )
            return claims
        except jwt.PyJWTError as e:
            raise ValueError(f"Invalid id_token: {e}")
