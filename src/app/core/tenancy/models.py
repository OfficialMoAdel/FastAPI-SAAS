"""Access to the tenant row type, supplied by the application seam.

``core.tenancy`` resolves tenants by slug and warms their cache, so it needs
the row type. Importing the tenants feature model here would be a
``core -> feature`` edge, which the H-5 contract forbids, so the model is
registered from outside instead.

The application seam is ``src.app.models`` — already documented as the single
model-registration point, already imported by both the web app and the ARQ
worker, and already exempt from the H-5 contract. It calls
:func:`register_tenant_model` on import, so both processes get the row type
without either of them naming a feature module.

:func:`ensure_tenant_model_registered` is the boot gate: an unwired process
fails at startup rather than on the first slug lookup, which is far harder to
diagnose. This mirrors the Principal provider seam, which is the established
pattern for exactly this problem.
"""

from __future__ import annotations

from typing import Any

from src.app.core.config import EnvironmentOption, settings
from src.app.core.logger import get_logger

logger = get_logger(__name__)

_tenant_model: type[Any] | None = None


def register_tenant_model(model: type[Any]) -> None:
    """Register the tenant row type. Called by the application seam."""
    global _tenant_model
    _tenant_model = model


def reset_tenant_model() -> None:
    """Reset the registered model (test isolation).

    TEST-ONLY, guarded against production for the same reason as
    ``reset_principal_provider``: a reset in production would fail the next
    request loudly, at a much worse moment than a boot failure.
    """
    if settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION:
        raise RuntimeError("reset_tenant_model is test-only and blocked in production")
    global _tenant_model
    _tenant_model = None


def require_tenant_model() -> type[Any]:
    """The tenant row type, or a loud failure when the seam is unwired."""
    if _tenant_model is None:
        raise RuntimeError(
            "tenant model is not registered. Import src.app.models (the single "
            "model-registration point) before resolving or warming tenants."
        )
    return _tenant_model


def ensure_tenant_model_registered() -> None:
    """Boot gate: raise when the seam is unwired. Call once at startup."""
    if _tenant_model is None:
        logger.error("tenant_model_unwired")
    require_tenant_model()


__all__ = [
    "ensure_tenant_model_registered",
    "register_tenant_model",
    "require_tenant_model",
    "reset_tenant_model",
]
