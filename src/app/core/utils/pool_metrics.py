"""Database connection pool metrics for observability.

Tracks SQLAlchemy connection pool health:
- Active connections
- Idle connections
- Pool overflow usage
- Connection checkout/checkin events
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event

logger = logging.getLogger(__name__)


@dataclass
class PoolMetrics:
    """Connection pool metrics snapshot."""

    pool_size: int = 0
    checked_out: int = 0
    checked_in: int = 0
    overflow: int = 0
    invalidated: int = 0

    # Event counters (since startup)
    total_checkouts: int = 0
    total_checkins: int = 0
    total_connections: int = 0
    total_invalidations: int = 0


# Global metrics instance
_metrics = PoolMetrics()


def get_pool_metrics() -> PoolMetrics:
    """Get current connection pool metrics."""
    return _metrics


def setup_pool_metrics(engine: Any) -> None:
    """Setup event listeners to track pool metrics.

    Args:
        engine: SQLAlchemy engine instance
    """
    pool = engine.pool

    # Get initial pool configuration
    _metrics.pool_size = pool.size()

    @event.listens_for(pool, "checkout")
    def on_checkout(dbapi_conn: Any, connection_record: Any, connection_proxy: Any) -> None:
        """Track connection checkout from pool."""
        _metrics.total_checkouts += 1
        _metrics.checked_out = pool.checkedout()
        _metrics.overflow = pool.overflow()

        logger.debug(
            "DB connection checkout | checked_out=%d | overflow=%d",
            _metrics.checked_out,
            _metrics.overflow,
        )

    @event.listens_for(pool, "checkin")
    def on_checkin(dbapi_conn: Any, connection_record: Any) -> None:
        """Track connection checkin to pool."""
        _metrics.total_checkins += 1
        _metrics.checked_in = pool.checkedin()

        logger.debug(
            "DB connection checkin | checked_in=%d",
            _metrics.checked_in,
        )

    @event.listens_for(pool, "connect")
    def on_connect(dbapi_conn: Any, connection_record: Any) -> None:
        """Track new connection creation."""
        _metrics.total_connections += 1
        logger.debug("New DB connection created | total=%d", _metrics.total_connections)

    @event.listens_for(pool, "invalidate")
    def on_invalidate(dbapi_conn: Any, connection_record: Any, exception: Any) -> None:
        """Track connection invalidation."""
        _metrics.total_invalidations += 1
        _metrics.invalidated += 1
        logger.warning(
            "DB connection invalidated | reason=%s | total_invalidations=%d",
            str(exception) if exception else "manual",
            _metrics.total_invalidations,
        )

    logger.info(
        "Connection pool metrics initialized | pool_size=%d | max_overflow=%d",
        pool.size(),
        pool._max_overflow,
    )


def log_pool_status(engine: Any) -> dict[str, int]:
    """Log and return current pool status.

    Args:
        engine: SQLAlchemy engine instance

    Returns:
        Dictionary with pool status
    """
    pool = engine.pool
    status = {
        "pool_size": pool.size(),
        "checked_out": pool.checkedout(),
        "checked_in": pool.checkedin(),
        "overflow": pool.overflow(),
        "total_checkouts": _metrics.total_checkouts,
        "total_checkins": _metrics.total_checkins,
        "total_connections": _metrics.total_connections,
        "total_invalidations": _metrics.total_invalidations,
    }

    logger.info(
        "Pool status | size=%d | checked_out=%d | checked_in=%d | overflow=%d",
        status["pool_size"],
        status["checked_out"],
        status["checked_in"],
        status["overflow"],
    )

    return status
