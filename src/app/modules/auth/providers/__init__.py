"""SSO Provider implementations.

Contains OAuth2 provider adapters for various identity providers.

Exports:
    - BaseOAuthProvider: Abstract base class for OAuth providers
    - GoogleProvider: Google OAuth2 implementation
    - GitHubProvider: GitHub OAuth2 implementation
    - MicrosoftProvider: Microsoft/Azure AD implementation
    - AppleProvider: Apple Sign In implementation
"""

from src.app.modules.auth.providers.apple import AppleProvider
from src.app.modules.auth.providers.base import BaseOAuthProvider
from src.app.modules.auth.providers.github import GitHubProvider
from src.app.modules.auth.providers.google import GoogleProvider
from src.app.modules.auth.providers.microsoft import MicrosoftProvider

__all__ = [
    "BaseOAuthProvider",
    "GoogleProvider",
    "GitHubProvider",
    "MicrosoftProvider",
    "AppleProvider",
]
