"""Central registry for L1 cache invalidation listeners (I11).

Every cross-worker L1 cache (tenant, auth-info, bitmask, tier-policy,
ban-negative) registers its :class:`InvalidationChannel` here at import
time. The app lifespan starts/stops ALL listeners through this module —
`_init_rate_limiting_and_security` / `_teardown_security_listeners` no
longer import five owner modules and hand-call five start/stop pairs.

Failure mode this closes: a new L1 cache adds its TTLCache + channel but
forgets the lifespan wiring, so its cross-worker invalidation publishes
into the void and every OTHER process serves stale suspension/revocation/
policy state until TTL expiry (the exact bug class behind the T9 and
R1-OPT listener additions).

Publishing stays on each owner's channel (`channel.publish`) — the
registry owns LIFECYCLE (register/start/stop), not the hot-path publish
call sites.
"""

from src.app.core.cache.invalidation_channel import InvalidationChannel
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# name -> channel. Registration happens at owner-module import time, so
# this dict is populated before the lifespan phase runs.
_CHANNELS: dict[str, InvalidationChannel] = {}


def register_l1_channel(name: str, channel: InvalidationChannel) -> InvalidationChannel:
    """Register an L1 invalidation channel under a stable name.

    Idempotent per name; re-registration with a different channel object
    (e.g. module re-import under a test runner) logs loudly and replaces.
    """
    existing = _CHANNELS.get(name)
    if existing is not None and existing is not channel:
        logger.warning("l1_channel_reregistered", name=name)
    _CHANNELS[name] = channel
    return channel


def start_all_l1_listeners() -> int:
    """Start every registered L1 listener. Returns the count started.

    Callers must first import every L1-owning module (see
    :func:`import_l1_owner_modules`) so registration has happened.
    """
    started = 0
    for name, channel in _CHANNELS.items():
        try:
            channel.start()
            started += 1
        except Exception as e:  # noqa: BLE001 - one bad listener must not block the rest
            logger.error("l1_listener_start_failed", name=name, error=str(e))
    logger.info("l1_listeners_started", count=started, channels=list(_CHANNELS))
    return started


async def stop_all_l1_listeners() -> int:
    """Stop every registered L1 listener. Returns the count stopped."""
    stopped = 0
    for name, channel in _CHANNELS.items():
        try:
            await channel.stop()
            stopped += 1
        except Exception as e:  # noqa: BLE001 - teardown must not mask shutdown
            logger.error("l1_listener_stop_failed", name=name, error=str(e))
    logger.info("l1_listeners_stopped", count=stopped)
    return stopped


def registered_l1_channels() -> tuple[str, ...]:
    """Names of all registered channels (for tests/observability)."""
    return tuple(_CHANNELS)


def import_l1_owner_modules() -> None:
    """Import every L1-owning module so its channel self-registers.

    Single enumeration point: adding an L1 means (1) registering its
    channel in the owner module and (2) adding one import here. Forgetting
    (2) is caught by contract tests asserting the expected channel count.

    Layering note: core/caching infrastructure intentionally enumerates the
    owner modules (four core, one module) — this is the composition point
    the lifespan delegates to, NOT a service-layer dependency.
    """
    from src.app.core.rbac import dependencies as _rbac_deps  # noqa: F401
    from src.app.core.security import auto_ban as _auto_ban  # noqa: F401
    from src.app.core.security import rate_limit_cache as _rate_limit_cache  # noqa: F401
    from src.app.core.tenancy import cache as _tenancy_cache  # noqa: F401
    from src.app.modules.users import cache as _users_cache  # noqa: F401


__all__ = [
    "import_l1_owner_modules",
    "register_l1_channel",
    "registered_l1_channels",
    "start_all_l1_listeners",
    "stop_all_l1_listeners",
]
