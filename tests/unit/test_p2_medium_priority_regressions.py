"""Unit tests for P2 Medium Priority Regressions (P2-1 to P2-5)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.env_validator import EnvironmentValidator
from src.app.middleware.ip_filter import IPFilterMiddleware
from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.billing.service import PricingService


class TestP2MediumPriorityRegressions:
    """Regression test suite for P2 Medium Priority fixes."""

    def test_p2_1_audit_log_model_has_tenant_action_index(self):
        """P2-1: AuditLog must declare idx_audit_tenant_action composite index."""
        table_args = getattr(AuditLog, "__table_args__", ())
        index_found = False
        for arg in table_args:
            if hasattr(arg, "name") and arg.name == "idx_audit_tenant_action":
                index_found = True
                break
        assert index_found is True

    @pytest.mark.asyncio
    async def test_p2_2_event_bus_batch_job_ttl(self):
        """P2-2: _publish_async_batch must enqueue jobs with 24h (86400000 ms) TTL."""
        from src.app.core.events import EventPriority
        from src.app.core.events.bus import EventBus

        bus = EventBus()
        mock_queue = MagicMock()
        mock_pipe = AsyncMock()
        mock_queue.pipeline.return_value.__aenter__.return_value = mock_pipe
        bus._arq_queue = mock_queue

        async def sample_handler(data):
            pass

        with patch("src.app.core.events.bus._pkg.arq_circuit.record_success", new_callable=AsyncMock):
            await bus._publish_async_batch("test.event", {"k": "v"}, [sample_handler], EventPriority.NORMAL)

        mock_pipe.psetex.assert_called_once()
        call_args = mock_pipe.psetex.call_args
        assert call_args[0][1] == 86400000  # Exactly 24 hours in ms

    def test_p2_3_ip_filter_exempts_health_and_metrics(self):
        """P2-3: IPFilterMiddleware must exempt health and metrics probes."""
        middleware = IPFilterMiddleware(app=MagicMock())
        assert middleware._is_exempt_path("/health") is True
        assert middleware._is_exempt_path("/health/live") is True
        assert middleware._is_exempt_path("/health/ready") is True
        assert middleware._is_exempt_path("/ready") is True
        assert middleware._is_exempt_path("/live") is True
        assert middleware._is_exempt_path("/metrics") is True
        assert middleware._is_exempt_path("/api/v1/health") is True
        # Documentation is not exempt from IP filtering / bot protection
        assert middleware._is_exempt_path("/openapi.json") is False
    @pytest.mark.asyncio
    async def test_p2_4_get_product_pricing_returns_total_count(self):
        """P2-4: get_product_pricing must request total_count metadata."""
        mock_db = AsyncMock()
        service = PricingService(mock_db)
        mock_product = MagicMock(id=10)
        mock_tenant = MagicMock(id=1)

        with patch("src.app.modules.billing.service.get_current_tenant", return_value=mock_tenant), \
             patch("src.app.modules.billing.service.crud_products.get", new_callable=AsyncMock) as mock_prod_get, \
             patch("src.app.modules.billing.service.crud_pricing.get_multi", new_callable=AsyncMock) as mock_get_multi:
            mock_prod_get.return_value = mock_product
            mock_get_multi.return_value = {"data": [], "total_count": 0}
            res = await service.get_product_pricing(product_id=10)

            assert "total_count" in res
            mock_get_multi.assert_called_once()
            assert mock_get_multi.call_args.kwargs.get("return_total_count") is True

    def test_p2_5_env_validator_ai_check_bounded(self):
        """P2-5: EnvironmentValidator._check_ai_providers handles unreachable host cleanly."""
        with patch.dict("os.environ", {"AI_PROVIDER": "ollama", "OLLAMA_BASE_URL": "http://127.0.0.1:9999"}):
            with patch("httpx.get", side_effect=Exception("Connection refused")):
                results = EnvironmentValidator._check_ai_providers()
                assert len(results) >= 1
                assert any("OLLAMA_BASE_URL" in r.key for r in results)
