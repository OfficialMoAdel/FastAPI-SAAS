"""Posts module - blog post management.

Components are imported lazily to avoid circular imports.
Cross-module imports MUST use the package root (P1-20: ruff TID252 bans
deep `posts.service` imports outside this package):
    from src.app.modules.posts import PostService
"""

from typing import TYPE_CHECKING

__all__ = [
    "PostService",
]

if TYPE_CHECKING:
    # Static-only re-export: gives mypy the real class for the lazy
    # __getattr__ facade below (P1-20). Never executed at runtime.
    from src.app.modules.posts.service import PostService as PostService


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "PostService":
        from src.app.modules.posts.service import PostService

        return PostService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
