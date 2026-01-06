"""Magic Link authentication module.

Provides passwordless authentication via email magic links.

Exports:
    - MagicLink: SQLAlchemy model for magic link tokens
    - MagicLinkService: Service layer for magic link operations
    - router: FastAPI router with magic link endpoints
    - Exceptions: InvalidMagicLinkError, MagicLinkAlreadyUsedError
"""

from src.app.modules.magic_link.exceptions import (
    InvalidMagicLinkError,
    MagicLinkAlreadyUsedError,
    MagicLinkRateLimitError,
    MagicLinkUserInactiveError,
)
from src.app.modules.magic_link.models import MagicLink
from src.app.modules.magic_link.router import router
from src.app.modules.magic_link.schemas import (
    MagicLinkSendRequest,
    MagicLinkSendResponse,
    MagicLinkVerifyRequest,
)
from src.app.modules.magic_link.service import MagicLinkService

__all__ = [
    "MagicLink",
    "MagicLinkService",
    "router",
    "InvalidMagicLinkError",
    "MagicLinkAlreadyUsedError",
    "MagicLinkRateLimitError",
    "MagicLinkUserInactiveError",
    "MagicLinkSendRequest",
    "MagicLinkSendResponse",
    "MagicLinkVerifyRequest",
]
