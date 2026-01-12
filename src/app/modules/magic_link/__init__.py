"""Magic Link authentication module.

Provides passwordless authentication via email magic links.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.magic_link.models import MagicLink
    from src.app.modules.magic_link.service import MagicLinkService
    from src.app.modules.magic_link.router import router as magic_link_router
    from src.app.modules.magic_link.exceptions import InvalidMagicLinkError
"""

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


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from .router import router

        return router
    if name == "MagicLinkService":
        from .service import MagicLinkService

        return MagicLinkService
    if name == "MagicLink":
        from .models import MagicLink

        return MagicLink
    if name in (
        "InvalidMagicLinkError",
        "MagicLinkAlreadyUsedError",
        "MagicLinkRateLimitError",
        "MagicLinkUserInactiveError",
    ):
        from . import exceptions

        return getattr(exceptions, name)
    if name in ("MagicLinkSendRequest", "MagicLinkSendResponse", "MagicLinkVerifyRequest"):
        from . import schemas

        return getattr(schemas, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
