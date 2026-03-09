"""Database connection pool metrics for observability.

Tracks SQLAlchemy connection pool health:
- Active connections
- Idle connections
- Pool overflow usage
- Connection checkout/checkin events
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import event

from src.app.core.logger import get_logger

logger = get_logger(__name__)


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

    def get_snapshot(self) -> dict[str, int]:
        """Get a dictionary snapshot of current metrics."""
        return {
            "pool_size": self.pool_size,
            "checked_out": self.checked_out,
            "checked_in": self.checked_in,
            "overflow": self.overflow,
            "total_checkouts": self.total_checkouts,
            "total_checkins": self.total_checkins,
            "total_connections": self.total_connections,
            "total_invalidations": self.total_invalidations,
        }


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

    Returns
    -------
        Dictionary with pool status
    """
    pool = engine.pool

    # Update current pool state metrics before returning
    _metrics.pool_size = pool.size()
    _metrics.checked_out = pool.checkedout()
    _metrics.checked_in = pool.checkedin()
    _metrics.overflow = pool.overflow()

    status = _metrics.get_snapshot()

    logger.info(
        "Pool status | size=%d | checked_out=%d | checked_in=%d | overflow=%d",
        status["pool_size"],
        status["checked_out"],
        status["checked_in"],
        status["overflow"],
    )

    return status
