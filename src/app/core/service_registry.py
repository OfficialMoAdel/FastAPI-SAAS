"""Service Registry for cross-module decoupling.

This module provides a centralized registry for service instances,
enabling modules to access other services without direct imports.

Benefits:
- Eliminates circular import issues
- Enables easy mocking in tests
- Provides clear dependency boundaries
- Supports lazy initialization
- Reuse service instances within the same request (via session caching)

Usage:
    # In service that needs cross-module access:
    from src.app.core.service_registry import get_service

    tier_svc = get_service("tier", db)
    tier = await tier_svc.get_by_id(tier_id)
"""

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.app.modules.rate_limits.service import RateLimitService
    from src.app.modules.tiers.service import TierService
    from src.app.modules.users.service import UserService


# Protocol definitions for type-safe cross-module access
class TierServiceProtocol(Protocol):
    """Protocol for TierService - defines expected interface."""

    async def get_tier_by_id(self, tier_id: int) -> dict[str, Any]: ...
    async def get_tiers(self, page: int = 1, items_per_page: int = 10) -> dict[str, Any]: ...


class RateLimitServiceProtocol(Protocol):
    """Protocol for RateLimitService - defines expected interface."""

    async def get_rate_limits_by_tier_id(self, tier_id: int) -> dict[str, Any]: ...


class UserServiceProtocol(Protocol):
    """Protocol for UserService - defines expected interface."""

    async def get(self, user_id: Any) -> Any: ...
    async def get_by_username(self, username: str) -> Any: ...
    async def get_by_email(self, email: str) -> Any: ...
    async def create(self, user: Any) -> Any: ...


# Service factory registry - maps service names to factory functions
_SERVICE_FACTORIES: dict[str, Any] = {}

# Cache: db_session -> {service_name: service_instance}
# Uses WeakKeyDictionary to automatically clean up when DB session is closed/garbage collected
_service_cache: WeakKeyDictionary[AsyncSession, dict[str, Any]] = WeakKeyDictionary()


def register_service(name: str, factory: Any) -> None:
    """Register a service factory.

    Args:
        name: Service identifier (e.g., "tier", "rate_limit", "user")
        factory: Factory function that takes db and returns service instance
    """
    _SERVICE_FACTORIES[name] = factory


def get_service(name: str, db: AsyncSession) -> Any:
    """Get a service instance by name.

    This function provides lazy loading of services, avoiding circular imports.
    It caches service instances per database session to reduce object allocation overhead.

    Args:
        name: Service identifier
        db: Database session

    Returns:
        Service instance

    Raises:
        KeyError: If service not registered
    """
    if name not in _SERVICE_FACTORIES:
        # Lazy registration on first access
        _register_default_services()

    factory = _SERVICE_FACTORIES.get(name)
    if factory is None:
        raise KeyError(f"Service '{name}' not registered. Available: {list(_SERVICE_FACTORIES.keys())}")

    # Get or create cache for this session
    session_cache = _service_cache.setdefault(db, {})

    # Return cached instance or create new
    if name not in session_cache:
        session_cache[name] = factory(db)

    return session_cache[name]


def _register_default_services() -> None:
    """Register default service factories (lazy loaded)."""
    if _SERVICE_FACTORIES:
        return  # Already registered

    # Import here to avoid circular imports at module load time
    from src.app.modules.api_keys.service import APIKeyService
    from src.app.modules.rate_limits.service import RateLimitService
    from src.app.modules.tiers.service import TierService
    from src.app.modules.users.service import UserService

    _SERVICE_FACTORIES["tier"] = lambda db: TierService(db)
    _SERVICE_FACTORIES["rate_limit"] = lambda db: RateLimitService(db)
    _SERVICE_FACTORIES["user"] = lambda db: UserService(db)
    _SERVICE_FACTORIES["api_key"] = lambda db: APIKeyService(db)


# Type aliases for IDE support
T = TypeVar("T")


def get_tier_service(db: AsyncSession) -> "TierService":
    """Get TierService instance (typed helper)."""
    return cast("TierService", get_service("tier", db))


def get_rate_limit_service(db: AsyncSession) -> "RateLimitService":
    """Get RateLimitService instance (typed helper)."""
    return cast("RateLimitService", get_service("rate_limit", db))


def get_user_service(db: AsyncSession) -> "UserService":
    """Get UserService instance (typed helper)."""
    return cast("UserService", get_service("user", db))
