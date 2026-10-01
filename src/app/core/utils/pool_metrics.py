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
            "db_connection_checkout",
            checked_out=_metrics.checked_out,
            overflow=_metrics.overflow,
        )

    @event.listens_for(pool, "checkin")
    def on_checkin(dbapi_conn: Any, connection_record: Any) -> None:
        """Track connection checkin to pool."""
        _metrics.total_checkins += 1
        _metrics.checked_in = pool.checkedin()

        logger.debug(
            "db_connection_checkin",
            checked_in=_metrics.checked_in,
        )

    @event.listens_for(pool, "connect")
    def on_connect(dbapi_conn: Any, connection_record: Any) -> None:
        """Track new connection creation."""
        _metrics.total_connections += 1
        logger.debug(
            "db_connection_created",
            total=_metrics.total_connections,
        )

    @event.listens_for(pool, "invalidate")
    def on_invalidate(dbapi_conn: Any, connection_record: Any, exception: Any) -> None:
        """Track connection invalidation."""
        _metrics.total_invalidations += 1
        _metrics.invalidated += 1
        logger.warning(
            "db_connection_invalidated",
            reason=str(exception) if exception else "manual",
            total_invalidations=_metrics.total_invalidations,
        )

    logger.info(
        "pool_metrics_initialized",
        pool_size=pool.size(),
        max_overflow=pool._max_overflow,
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
        "pool_status",
        size=status["pool_size"],
        checked_out=status["checked_out"],
        checked_in=status["checked_in"],
        overflow=status["overflow"],
    )

    return status
