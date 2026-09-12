"""P2-7 regression tests: perf-polish bounds and constants.

- Attack header scan set is a frozen module constant (12 headers).
- GZip default compresslevel is 4 (CPU/size trade for a JSON API).
- Middleware QueryStats caps recorded detail but keeps a truthful total.
- Notification render_template caches compiled templates by content hash.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.middleware.attack_detection import _HEADERS_TO_CHECK
from src.app.middleware.gzip_filter import GZipFilterMiddleware
from src.app.middleware.query_profiler import MAX_QUERIES_RECORDED, QueryStats


class TestP27PerfPolish:
    def test_headers_to_check_is_frozen_twelve(self) -> None:
        assert isinstance(_HEADERS_TO_CHECK, tuple)
        assert len(_HEADERS_TO_CHECK) == 12
        assert "cookie" in _HEADERS_TO_CHECK
        assert "authorization" in _HEADERS_TO_CHECK

    def test_gzip_default_compresslevel_is_4(self) -> None:
        mw = GZipFilterMiddleware(app=MagicMock())
        assert mw.compresslevel == 4
        assert mw.minimum_size == 500

    def test_query_stats_caps_recorded_detail(self) -> None:
        stats = QueryStats()
        for i in range(MAX_QUERIES_RECORDED + 50):
            stats.add_query(f"SELECT {i}", {}, 1.0)
        assert stats.total_query_count == MAX_QUERIES_RECORDED + 50
        assert len(stats.queries) == MAX_QUERIES_RECORDED
        assert stats.total_time_ms == MAX_QUERIES_RECORDED + 50


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def service(mock_db):
    from src.app.modules.notifications.service import NotificationService

    return NotificationService(db=mock_db)


@pytest.fixture(autouse=True)
def _clear_template_cache():
    from src.app.modules.notifications import service as svc_mod

    svc_mod._COMPILED_TEMPLATE_CACHE.clear()
    yield
    svc_mod._COMPILED_TEMPLATE_CACHE.clear()


class TestP27TemplateCache:
    @pytest.mark.asyncio
    async def test_render_reuses_compiled_template(self, service) -> None:
        from src.app.modules.notifications import service as svc_mod

        t_id = uuid4()
        with patch.object(service, "get_template_content", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = ("Subj", "Hello {{name}}")
            _, html1 = await service.render_template(t_id, "tpl", {"name": "A"})
            _, html2 = await service.render_template(t_id, "tpl", {"name": "B"})
        assert html1 == "Hello A"
        assert html2 == "Hello B"
        assert len(svc_mod._COMPILED_TEMPLATE_CACHE) == 1

    @pytest.mark.asyncio
    async def test_cache_evicts_fifo_at_bound(self, service, monkeypatch) -> None:
        from src.app.modules.notifications import service as svc_mod

        monkeypatch.setattr(svc_mod, "_MAX_CACHED_TEMPLATES", 2)
        t_id = uuid4()
        with patch.object(service, "get_template_content", new_callable=AsyncMock) as mock_get:
            for name in ("a", "b", "c"):
                mock_get.return_value = ("Subj", f"Hello {name} {{{{x}}}}")
                await service.render_template(t_id, "tpl", {"x": "!"})
        assert len(svc_mod._COMPILED_TEMPLATE_CACHE) == 2
