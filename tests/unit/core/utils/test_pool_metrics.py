from unittest.mock import MagicMock, patch

import pytest

from src.app.core.utils.pool_metrics import (
    PoolMetrics,
    _metrics,
    get_pool_metrics,
    log_pool_status,
    setup_pool_metrics,
)


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset global metrics before each test."""
    _metrics.pool_size = 0
    _metrics.checked_out = 0
    _metrics.checked_in = 0
    _metrics.overflow = 0
    _metrics.invalidated = 0
    _metrics.total_checkouts = 0
    _metrics.total_checkins = 0
    _metrics.total_connections = 0
    _metrics.total_invalidations = 0
    yield


def test_get_pool_metrics() -> None:
    metrics = get_pool_metrics()
    assert isinstance(metrics, PoolMetrics)
    assert metrics is _metrics


def test_setup_pool_metrics_and_events() -> None:
    mock_engine = MagicMock()
    mock_pool = MagicMock()
    mock_engine.pool = mock_pool

    mock_pool.size.return_value = 10
    mock_pool._max_overflow = 5

    # Track registered listeners
    listeners = {}

    with patch("src.app.core.utils.pool_metrics.event.listens_for") as mock_listens_for:
        def side_effect(target, identifier):
            def decorator(fn):
                listeners[identifier] = fn
                return fn
            return decorator

        mock_listens_for.side_effect = side_effect

        setup_pool_metrics(mock_engine)

        assert _metrics.pool_size == 10
        assert "checkout" in listeners
        assert "checkin" in listeners
        assert "connect" in listeners
        assert "invalidate" in listeners

        # Test checkout event
        mock_pool.checkedout.return_value = 3
        mock_pool.overflow.return_value = 1
        listeners["checkout"](None, None, None)
        assert _metrics.total_checkouts == 1
        assert _metrics.checked_out == 3
        assert _metrics.overflow == 1

        # Test checkin event
        mock_pool.checkedin.return_value = 7
        listeners["checkin"](None, None)
        assert _metrics.total_checkins == 1
        assert _metrics.checked_in == 7

        # Test connect event
        listeners["connect"](None, None)
        assert _metrics.total_connections == 1

        # Test invalidate event
        listeners["invalidate"](None, None, Exception("test exception"))
        assert _metrics.total_invalidations == 1
        assert _metrics.invalidated == 1


def test_log_pool_status() -> None:
    mock_engine = MagicMock()
    mock_pool = MagicMock()
    mock_engine.pool = mock_pool

    mock_pool.size.return_value = 15
    mock_pool.checkedout.return_value = 5
    mock_pool.checkedin.return_value = 10
    mock_pool.overflow.return_value = 2

    # set some counters
    _metrics.total_checkouts = 42

    with patch("src.app.core.utils.pool_metrics.logger.info") as mock_logger:
        status_dict = log_pool_status(mock_engine)

        assert _metrics.pool_size == 15
        assert _metrics.checked_out == 5
        assert _metrics.checked_in == 10
        assert _metrics.overflow == 2

        assert status_dict["pool_size"] == 15
        assert status_dict["checked_out"] == 5
        assert status_dict["checked_in"] == 10
        assert status_dict["overflow"] == 2
        assert status_dict["total_checkouts"] == 42

        mock_logger.assert_called_once()
