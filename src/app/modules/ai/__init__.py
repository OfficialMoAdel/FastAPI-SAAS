"""AI Features module.

Provides AI chat session management, message handling with token tracking,
and user AI preferences via Ollama integration.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.ai.models import AIChatSession, AIChatMessage, AISettings
    from src.app.modules.ai.service import chat_service
    from src.app.modules.ai.router import router as ai_router
"""

__all__ = [
    "router",
    "AIChatSession",
    "AIChatMessage",
    "AISettings",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from .router import router

        return router
    if name == "AIChatSession":
        from .models import AIChatSession

        return AIChatSession
    if name == "AIChatMessage":
        from .models import AIChatMessage

        return AIChatMessage
    if name == "AISettings":
        from .models import AISettings

        return AISettings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
