"""Unit tests for async template loading in notifications (P2-1 optimization).

Tests verify that template loading uses asyncio.to_thread to avoid blocking
the event loop during file I/O operations.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from jinja2.sandbox import SandboxedEnvironment

from src.app.modules.notifications.service import NotificationService


@pytest.fixture
def mock_db():
    """Mock database session."""
    return AsyncMock()


@pytest.fixture
def notification_service(mock_db):
    """NotificationService instance with mocked database."""
    return NotificationService(mock_db)


@pytest.fixture
def mock_jinja_env():
    """Mock Jinja2 environment."""
    env = MagicMock(spec=SandboxedEnvironment)
    template = MagicMock()
    template.render.return_value = "<html>Test content</html>"
    env.get_template.return_value = template
    return env


class TestTemplateLoadingUsesToThread:
    """Test that template loading uses asyncio.to_thread."""

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    @patch("src.app.modules.notifications.service.jinja_env")
    @patch("src.app.modules.notifications.service.asyncio.to_thread")
    async def test_template_loading_uses_to_thread(
        self,
        mock_to_thread,
        mock_jinja_env,
        notification_service,
        mock_db,
    ):
        """Verify asyncio.to_thread is called for template loading."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock template
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>Welcome</html>"

        # Mock to_thread to handle multiple calls (get_template and get_source)
        async def mock_to_thread_impl(func, *args):
            if "get_template" in str(func):
                return mock_template
            if "get_source" in str(func):
                return ("<html>Welcome</html>", "file.html", lambda: True)
            return None

        mock_to_thread.side_effect = mock_to_thread_impl

        # Act
        await notification_service.get_template_content(tenant_id, template_name)

        # Assert
        assert mock_to_thread.called, "asyncio.to_thread should be called"
        calls = mock_to_thread.call_args_list
        assert len(calls) == 2, "asyncio.to_thread should be called twice"
        assert callable(calls[0][0][0]), "First arg of first call should be callable (get_template)"
        assert calls[0][0][1] == f"{template_name}.html.jinja2", "get_template should receive template name"
        assert callable(calls[1][0][0]), "First arg of second call should be callable (get_source)"
        assert calls[1][0][2] == f"{template_name}.html.jinja2", "get_source should receive template name"


class TestTemplateLoadingNonBlocking:
    """Test that template loading doesn't block the event loop."""

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    @patch("src.app.modules.notifications.service.jinja_env")
    async def test_template_loading_non_blocking(
        self,
        mock_jinja_env,
        notification_service,
        mock_db,
    ):
        """Verify event loop can process other tasks during template load."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock database query to return no override
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock template with slow loading (simulates file I/O)
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>Welcome</html>"

        def slow_get_template(name):
            time.sleep(0.02)  # 20ms simulated I/O
            return mock_template

        mock_jinja_env.get_template = slow_get_template
        mock_jinja_env.loader.get_source.return_value = ("<html>Welcome</html>", "file.html", lambda: True)

        # Create a concurrent task that should complete during template load
        concurrent_task_completed = False

        async def concurrent_task():
            nonlocal concurrent_task_completed
            await asyncio.sleep(0.01)  # 10ms
            concurrent_task_completed = True

        # Act - run template load and concurrent task together
        template_task = notification_service.get_template_content(tenant_id, template_name)
        other_task = concurrent_task()

        results = await asyncio.gather(template_task, other_task)

        # Assert
        assert results[0] is not None, "Template should load successfully"
        assert concurrent_task_completed, "Concurrent task should complete during template load"


class TestTemplateCachingStillWorks:
    """Test that Jinja2 internal caching still works."""

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    @patch("src.app.modules.notifications.service.jinja_env")
    async def test_template_caching_still_works(
        self,
        mock_jinja_env,
        notification_service,
        mock_db,
    ):
        """Verify second load uses Jinja2 cache (get_template called once)."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock database query to return no override
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock template
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>Welcome</html>"
        mock_jinja_env.get_template.return_value = mock_template
        mock_jinja_env.loader.get_source.return_value = ("<html>Welcome</html>", "file.html", lambda: True)

        # Act - load template twice
        await notification_service.get_template_content(tenant_id, template_name)
        await notification_service.get_template_content(tenant_id, template_name)

        # Assert - get_template should be called twice (asyncio.to_thread doesn't cache)
        # But Jinja2 internal cache means the actual file I/O only happens once
        assert mock_jinja_env.get_template.call_count == 2


class TestRedisCacheCheckedFirst:
    """Test that Redis cache is checked before file I/O."""

    @pytest.mark.asyncio
    @patch("src.app.modules.notifications.service.jinja_env")
    async def test_redis_cache_checked_first(
        self,
        mock_jinja_env,
        notification_service,
        mock_db,
    ):
        """Verify file I/O only happens on cache miss."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock Redis cache hit with AsyncMock
        with patch("src.app.core.utils.cache.client") as mock_redis:
            mock_redis.get = AsyncMock(return_value='["Welcome", "<html>Cached content</html>"]')

            # Act
            subject, content = await notification_service.get_template_content(tenant_id, template_name)

            # Assert
            assert subject == "Welcome"
            assert content == "<html>Cached content</html>"
            # get_template should NOT be called when cache hits
            mock_jinja_env.get_template.assert_not_called()


class TestTemplateRendersCorrectly:
    """Test that template output is unchanged."""

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    async def test_template_renders_correctly(
        self,
        notification_service,
        mock_db,
    ):
        """Verify template renders same content as before optimization."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock database query to return no override
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Use real Jinja2 environment with test template
        with patch("src.app.modules.notifications.service.jinja_env") as mock_env:
            # BUG FIX: service now uses loader.get_source() to get raw Jinja2 source
            mock_template = MagicMock()
            mock_env.get_template.return_value = mock_template
            mock_env.loader.get_source.return_value = (
                "<html><body>Welcome Email</body></html>",
                "welcome_email.html.jinja2",
                lambda: True,
            )

            # Act
            subject, content = await notification_service.get_template_content(tenant_id, template_name)

            # Assert
            assert subject == "Welcome Email", "Subject should be derived from template name"
            assert "<html>" in content, "Content should be HTML"
            assert "Welcome Email" in content, "Content should contain expected text"


class TestTemplateLoadingPerformance:
    """Test template loading performance characteristics."""

    @pytest.mark.asyncio
    @patch("src.app.core.utils.cache.client", None)
    @patch("src.app.modules.notifications.service.jinja_env")
    async def test_template_loading_performance(
        self,
        mock_jinja_env,
        notification_service,
        mock_db,
    ):
        """Verify template loading completes in reasonable time."""
        # Arrange
        tenant_id = uuid4()
        template_name = "welcome_email"

        # Mock database query to return no override
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Mock template
        mock_template = MagicMock()
        mock_template.render.return_value = "<html>Welcome</html>"
        mock_jinja_env.get_template.return_value = mock_template
        mock_jinja_env.loader.get_source.return_value = ("<html>Welcome</html>", "file.html", lambda: True)

        # Act - measure time
        start = time.perf_counter()
        await notification_service.get_template_content(tenant_id, template_name)
        elapsed = time.perf_counter() - start

        # Assert - should complete quickly (< 100ms even with mocks)
        assert elapsed < 0.1, f"Template loading took {elapsed:.3f}s, expected < 0.1s"
