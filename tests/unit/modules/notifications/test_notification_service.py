import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, UTC

from src.app.modules.notifications.models import Notification, NotificationStatus, EmailTemplateOverride
from src.app.modules.notifications.service import NotificationService, send_email_task, handle_notification_send_event
from src.app.modules.user_preferences.models import UserPreferences
from src.app.modules.billing.models import StripeSettings, SubscriptionPlan, UserSubscription
from src.app.modules.users.models import User
from src.app.modules.ai.models import AIChatSession
from src.app.modules.tenants.models import Tenant

@pytest.fixture
def mock_db():
    return AsyncMock()

@pytest.fixture
def service(mock_db):
    return NotificationService(db=mock_db)

def make_mock_notification(id=1, status=NotificationStatus.PENDING):
    m = MagicMock()
    m.id = uuid4()
    m.status = status
    m.created_at = datetime.now(UTC)
    return m

def make_mock_override(id=1, content="override content"):
    m = MagicMock()
    m.id = id
    m.subject_override = "Custom Subject"
    m.content = content
    return m

def make_mock_user_prefs(enabled=True, disabled_types=[], quiet_enabled=False):
    m = MagicMock()
    m.email_notifications_enabled = enabled
    m.disabled_notification_types = disabled_types
    m.quiet_hours_enabled = quiet_enabled
    return m

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.redis_client")
async def test_get_template_content_cache_hit(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=b'["Subject", "<html></html>"]')
    t_id = uuid4()
    
    subj, content = await service.get_template_content(t_id, "welcome")
    
    assert subj == "Subject"
    assert content == "<html></html>"
    mock_db.execute.assert_not_called()

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.redis_client")
async def test_get_template_content_override(mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    t_id = uuid4()
    
    mock_override = make_mock_override()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_override
    mock_db.execute = AsyncMock(return_value=mock_result)
    
    subj, content = await service.get_template_content(t_id, "welcome")
    
    assert subj == "Custom Subject"
    assert content == "override content"

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.redis_client")
@patch("src.app.modules.notifications.service.jinja_env")
async def test_get_template_content_fallback(mock_jinja, mock_redis, service, mock_db):
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    t_id = uuid4()
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)
    
    mock_template = MagicMock()
    mock_template.render.return_value = "<p>File Template</p>"
    mock_jinja.get_template = MagicMock(return_value=mock_template)
    
    subj, content = await service.get_template_content(t_id, "welcome")
    
    assert subj == "Welcome"
    assert content == "<p>File Template</p>"

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.jinja_env")
async def test_render_template(mock_jinja, service, mock_db):
    t_id = uuid4()
    
    with patch.object(service, "get_template_content", new_callable=AsyncMock) as mock_get_content:
        mock_get_content.return_value = ("Test Subj", "Hello {{name}}")
        
        mock_template_obj = MagicMock()
        mock_template_obj.render.return_value = "Hello Bob"
        mock_jinja.from_string.return_value = mock_template_obj
        
        subj, html = await service.render_template(t_id, "test_tpl", {"name": "Bob"})
        assert subj == "Test Subj"
        assert html == "Hello Bob"

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.rate_limiter")
@patch("src.app.modules.notifications.service.event_bus")
async def test_send_email_success(mock_event_bus, mock_rate_limiter, service, mock_db):
    t_id = uuid4()
    mock_rate_limiter.check_rate_limit = AsyncMock(return_value=(False, {}))
    mock_event_bus.publish = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    
    with patch.object(service, "render_template", new_callable=AsyncMock) as mock_render:
        mock_render.return_value = ("Subj", "<html></html>")
        
        notification = await service.send_email(
            t_id, "user@test.com", "welcome", {"name": "Test"}
        )
        
        assert notification.status == NotificationStatus.PENDING
        mock_db.add.assert_called_once()
        mock_event_bus.publish.assert_called_once()

@pytest.mark.asyncio
async def test_send_email_skipped_by_prefs(service, mock_db):
    t_id = uuid4()
    u_id = 99
    
    with patch.object(service, "should_send_notification", new_callable=AsyncMock) as mock_should:
        mock_should.return_value = False
        
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        
        notification = await service.send_email(
            t_id, "user@test.com", "welcome", {}, user_id=u_id
        )
        
        assert notification.status == NotificationStatus.FAILED
        assert notification.error_message == "Skipped by user preferences"

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.rate_limiter")
async def test_send_email_rate_limit_exceeded(mock_rate_limiter, service, mock_db):
    t_id = uuid4()
    mock_rate_limiter.check_rate_limit = AsyncMock(return_value=(True, {}))
    
    with pytest.raises(ValueError, match="Rate limit exceeded"):
        await service.send_email(t_id, "user@test.com", "welcome", {})

@pytest.mark.asyncio
async def test_mark_sent(service, mock_db):
    n_id = uuid4()
    mock_notif = make_mock_notification()
    mock_db.get = AsyncMock(return_value=mock_notif)
    mock_db.commit = AsyncMock()
    
    await service.mark_sent(n_id)
    
    assert mock_notif.status == NotificationStatus.SENT
    mock_db.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_mark_failed(service, mock_db):
    n_id = uuid4()
    mock_notif = make_mock_notification()
    mock_db.get = AsyncMock(return_value=mock_notif)
    mock_db.commit = AsyncMock()
    
    await service.mark_failed(n_id, "error msg")
    
    assert mock_notif.status == NotificationStatus.FAILED
    assert mock_notif.error_message == "error msg"

@pytest.mark.asyncio
async def test_get_notifications(service, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 5
    mock_result_list = MagicMock()
    mock_result_list.scalars().all.return_value = [make_mock_notification(), make_mock_notification()]
    
    mock_db.execute = AsyncMock(side_effect=[mock_result, mock_result_list])
    
    total, items = await service.get_notifications(uuid4())
    assert total == 5
    assert len(items) == 2

@pytest.mark.asyncio
async def test_should_send_notification_disabled(service, mock_db):
    mock_prefs = make_mock_user_prefs(enabled=False)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_prefs
    mock_db.execute = AsyncMock(return_value=mock_result)
    
    result = await service.should_send_notification(1, "welcome")
    assert result is False

@pytest.mark.asyncio
async def test_should_send_notification_type_disabled(service, mock_db):
    mock_prefs = make_mock_user_prefs(enabled=True, disabled_types=["welcome"])
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_prefs
    mock_db.execute = AsyncMock(return_value=mock_result)
    
    result = await service.should_send_notification(1, "welcome")
    assert result is False

@pytest.mark.asyncio
async def test_should_send_notification_no_prefs(service, mock_db):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)
    
    result = await service.should_send_notification(1, "welcome")
    assert result is True

@pytest.mark.asyncio
async def test_template_override_crud(service, mock_db):
    t_id = uuid4()
    
    # List
    mock_list_res = MagicMock()
    mock_list_res.scalars().all.return_value = [make_mock_override()]
    mock_db.execute = AsyncMock(return_value=mock_list_res)
    overrides = await service.list_template_overrides(t_id)
    assert len(overrides) == 1
    
    # Delete False
    mock_empty_res = MagicMock()
    mock_empty_res.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_empty_res)
    res = await service.delete_template_override(t_id, "tpl")
    assert res is False

    # Delete True
    with patch("src.app.modules.notifications.service.redis_client") as rclient:
        rclient.delete = AsyncMock()
        mock_override = make_mock_override()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_override
        mock_db.execute = AsyncMock(return_value=mock_res)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()
        
        res = await service.delete_template_override(t_id, "tpl")
        assert res is True
        mock_db.delete.assert_awaited_once_with(mock_override)

@pytest.mark.asyncio
@patch("src.app.modules.notifications.service.redis_client")
async def test_create_template_override(mock_redis, service, mock_db):
    t_id = uuid4()
    mock_redis.delete = AsyncMock()
    
    # Existing
    mock_existing = make_mock_override()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_existing
    mock_db.execute = AsyncMock(return_value=mock_res)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    
    override = await service.create_template_override(t_id, "tpl", "subj", "content")
    assert override.subject_override == "subj"
    
    # New
    mock_res.scalar_one_or_none.return_value = None
    mock_db.add = MagicMock()
    
    new_override = await service.create_template_override(t_id, "tpl", "subj", "content")
    mock_db.add.assert_called_once()
    assert new_override.subject_override == "subj"

@pytest.mark.asyncio
@patch("src.app.core.db.database.async_session_maker")
@patch("src.app.modules.notifications.service.aiosmtplib")
async def test_send_email_task(mock_smtp, mock_session_maker):
    mock_db = AsyncMock()
    # async with async_session_maker() as db -> return mock_db
    mock_session_maker.return_value.__aenter__.return_value = mock_db

    with patch("src.app.modules.notifications.service.NotificationService") as mock_srv_cls:
        mock_srv = AsyncMock()
        mock_srv_cls.return_value = mock_srv
        
        mock_smtp.send = AsyncMock()
        
        await send_email_task({}, "eb000000-0000-0000-0000-000000000000", "test@test.com", "subj", "<html>")
        
        mock_smtp.send.assert_awaited_once()
        mock_srv.mark_sent.assert_awaited_once()

@pytest.mark.asyncio
@patch("src.app.core.utils.queue.pool")
async def test_handle_notification_send_event(mock_arq):
    mock_arq.enqueue_job = AsyncMock()
    
    # Missing data
    await handle_notification_send_event({})
    mock_arq.enqueue_job.assert_not_called()
    
    # Success
    data = {
        "notification_id": "123",
        "to_email": "test@test",
        "subject": "sub",
        "html_content": "html",
        "tenant_id": "t1"
    }
    await handle_notification_send_event(data)
    mock_arq.enqueue_job.assert_awaited_once()
