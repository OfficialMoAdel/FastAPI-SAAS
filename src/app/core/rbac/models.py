"""Access to the RBAC row types, supplied by the application seam.

``core.rbac`` resolves a user's permissions by joining four tables, so it needs
their row types. Importing the rbac feature module would be a
``core -> feature`` edge, which the H-5 contract forbids, so they are
registered from outside instead.

The join needs all four together, so they are registered as one set rather
than four separate hooks: a partially-registered graph would fail deep inside
the join with an obscure error, whereas a missing graph fails immediately
and by name.

The application seam is ``src.app.models`` — already documented as the single
model-registration point, already imported by both the web app and the ARQ
worker, and already exempt from the H-5 contract. It calls
:func:`register_permission_graph` on import.

:func:`ensure_permission_graph_registered` is the boot gate. This is the same
shape as the Principal provider and tenant-model seams.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PermissionGraph:
    """The four row types a permission lookup walks, as one registered set.

    Named for their role in the join rather than their class names, so the
    query reads as the traversal it is: user -> roles -> role permissions ->
    permissions.
    """

    user_role: type[Any]
    role: type[Any]
    role_permission: type[Any]
    permission: type[Any]


_permission_graph: PermissionGraph | None = None


def register_permission_graph(graph: PermissionGraph) -> None:
    """Register the permission join's row types. Called by the application seam."""
    global _permission_graph
    _permission_graph = graph


def reset_permission_graph() -> None:
    """Reset the registered graph (test isolation).

    TEST-ONLY, blocked in production: a reset there would fail the next
    permission check at request time rather than the next boot.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_permission_graph is test-only and blocked in production")
    global _permission_graph
    _permission_graph = None


def require_permission_graph() -> PermissionGraph:
    """The registered row types, or a loud failure when the seam is unwired."""
    if _permission_graph is None:
        raise RuntimeError(
            "permission graph is not registered. Import src.app.models (the "
            "single model-registration point) before resolving permissions."
        )
    return _permission_graph


def ensure_permission_graph_registered() -> None:
    """Boot gate: raise when the seam is unwired. Call once at startup."""
    if _permission_graph is None:
        logger.error("permission_graph_unwired")
    require_permission_graph()


__all__ = [
    "PermissionGraph",
    "ensure_permission_graph_registered",
    "register_permission_graph",
    "require_permission_graph",
    "reset_permission_graph",
]
