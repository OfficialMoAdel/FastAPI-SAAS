"""SSO Provider implementations.

Contains OAuth2 provider adapters for various identity providers.

Exports:
    - BaseOAuthProvider: Abstract base class for OAuth providers
    - GoogleProvider: Google OAuth2 implementation
    - GitHubProvider: GitHub OAuth2 implementation
    - MicrosoftProvider: Microsoft/Azure AD implementation
    - AppleProvider: Apple Sign In implementation
"""

__all__ = [
    "BaseOAuthProvider",
    "GoogleProvider",
    "GitHubProvider",
    "MicrosoftProvider",
    "AppleProvider",
]
