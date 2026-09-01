from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app.api.deps import RequestContext


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_tenant():
    tenant = MagicMock()
    tenant.id = uuid4()
    return tenant


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = 1
    return user


@pytest.fixture
def mock_ctx(mock_db, mock_tenant, mock_user):
    """RequestContext-style principal bundling db/tenant/user for direct endpoint calls."""
    ctx = MagicMock(spec=RequestContext)
    ctx.db = mock_db
    ctx.tenant = mock_tenant
    ctx.user = mock_user
    ctx.tenant_id = mock_tenant.id
    ctx.user_id = mock_user.id
    return ctx


# Since using `client` requires dealing with JWTs and real app setup, and we want unit tests,
# it is much faster and more isolated to just call the router functions directly!

from fastapi import HTTPException

from src.app.modules.audit_logs.router import (
    export_audit_logs,
    get_audit_log,
    get_export_count,
    list_audit_logs,
    list_available_actions,
)


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_list_audit_logs_direct(mock_service_cls, mock_ctx):
    mock_service = MagicMock()

    mock_log = MagicMock()
    mock_log.id = uuid4()
    mock_log.tenant_id = mock_ctx.tenant_id
    mock_log.user_id = 1
    mock_log.action = "test.action"
    mock_log.entity_type = "test"
    mock_log.entity_id = "123"
    mock_log.details = {}
    mock_log.ip_address = "127.0.0.1"
    mock_log.user_agent = "agent"
    mock_log.created_at = datetime.now(UTC)

    mock_service.query_logs_with_total = AsyncMock(return_value=([mock_log], 1))
    mock_service_cls.return_value = mock_service

    from src.app.core.pagination import PaginationParams

    result = await list_audit_logs(
        ctx=mock_ctx,
        action=None,
        user_id=None,
        entity_type=None,
        start_date=None,
        end_date=None,
        pagination=PaginationParams(page=1, per_page=10),
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0].action == "test.action"


@pytest.mark.asyncio
async def test_get_audit_log(mock_ctx):
    log_id = uuid4()

    mock_log = MagicMock()
    mock_log.id = log_id
    mock_log.tenant_id = mock_ctx.tenant_id
    mock_log.action = "test.action"
    mock_log.created_at = datetime.now(UTC)
    mock_log.user_id = None
    mock_log.entity_type = None
    mock_log.entity_id = None
    mock_log.details = {}
    mock_log.ip_address = None
    mock_log.user_agent = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_log
    mock_ctx.db.execute = AsyncMock(return_value=mock_result)

    result = await get_audit_log(log_id=log_id, ctx=mock_ctx)

    assert result.id == log_id
    assert result.action == "test.action"


@pytest.mark.asyncio
async def test_get_audit_log_not_found(mock_ctx):
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_ctx.db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc:
        await get_audit_log(log_id=uuid4(), ctx=mock_ctx)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_available_actions(mock_ctx):
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = ["user.login", "user.logout"]
    mock_result.scalars.return_value = mock_scalars
    mock_ctx.db.execute = AsyncMock(return_value=mock_result)

    result = await list_available_actions(ctx=mock_ctx)

    assert result == ["user.login", "user.logout"]


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_get_export_count(mock_service_cls, mock_ctx):
    mock_service = MagicMock()
    mock_service.export_count = AsyncMock(return_value=50)
    mock_service_cls.return_value = mock_service

    result = await get_export_count(
        ctx=mock_ctx,
        action="user.login",
        user_id=1,
        entity_type="user",
        start_date=None,
        end_date=None,
    )

    assert result.count == 50
    assert result.can_export is True


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_audit_logs_too_many(mock_service_cls, mock_ctx):
    mock_service = MagicMock()
    # EXPORT_MAX_RECORDS is 100_000, return more to trigger error
    mock_service.export_count = AsyncMock(return_value=150000)
    mock_service_cls.return_value = mock_service

    with pytest.raises(HTTPException) as exc:
        await export_audit_logs(
            ctx=mock_ctx,
            format="csv",
            action=None,
            user_id=None,
            entity_type=None,
            start_date=None,
            end_date=None,
        )

    assert exc.value.status_code == 400
    assert "Export exceeds maximum" in str(exc.value.detail)


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_audit_logs_csv(mock_service_cls, mock_ctx):
    mock_service = MagicMock()
    mock_service.create_log = AsyncMock()
    mock_service_cls.return_value = mock_service

    # A13: count comes from service.export_count, not raw db.execute
    mock_service.export_count = AsyncMock(return_value=2)

    # Mock stream
    mock_log = MagicMock()
    mock_log.id = uuid4()
    mock_log.tenant_id = mock_ctx.tenant_id
    mock_log.user_id = 1
    mock_log.action = "test.action"
    mock_log.entity_type = "test"
    mock_log.entity_id = "123"
    mock_log.details = {"key": "value"}
    mock_log.ip_address = "127.0.0.1"
    mock_log.user_agent = "agent"
    mock_log.created_at = datetime.now(UTC)

    async def async_gen():
        yield mock_log

    mock_stream_result = MagicMock()
    mock_stream_result.scalars.return_value = async_gen()
    mock_ctx.db.stream = AsyncMock(return_value=mock_stream_result)

    result = await export_audit_logs(
        ctx=mock_ctx,
        format="csv",
        action="test.action",
        user_id=1,
        entity_type="test",
        start_date=datetime.now(UTC),
        end_date=datetime.now(UTC),
    )

    assert result.media_type == "text/csv"

    # To test streaming response content
    body = b""
    async for chunk in result.body_iterator:
        if isinstance(chunk, str):
            body += chunk.encode("utf-8")
        else:
            body += chunk

    content = body.decode("utf-8")
    assert "test.action" in content


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_audit_logs_json(mock_service_cls, mock_ctx):
    mock_service = MagicMock()
    mock_service.create_log = AsyncMock()
    mock_service_cls.return_value = mock_service

    mock_service.export_count = AsyncMock(return_value=1)

    mock_log = MagicMock()
    mock_log.id = uuid4()
    mock_log.action = "test.action"
    mock_log.created_at = datetime.now(UTC)
    mock_log.user_id = None
    mock_log.entity_type = None
    mock_log.entity_id = None
    mock_log.details = None
    mock_log.ip_address = None
    mock_log.user_agent = None

    async def async_gen():
        yield mock_log

    mock_stream_result = MagicMock()
    mock_stream_result.scalars.return_value = async_gen()
    mock_ctx.db.stream = AsyncMock(return_value=mock_stream_result)

    result = await export_audit_logs(
        ctx=mock_ctx,
        format="json",
        action=None,
        user_id=None,
        entity_type=None,
        start_date=None,
        end_date=None,
    )

    assert result.media_type == "application/json"


@pytest.mark.asyncio
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_audit_logs_jsonl(mock_service_cls, mock_ctx):
    mock_service = MagicMock()
    mock_service.create_log = AsyncMock()
    mock_service_cls.return_value = mock_service

    mock_service.export_count = AsyncMock(return_value=1)

    mock_log = MagicMock()
    mock_log.id = uuid4()
    mock_log.action = "test.action"
    mock_log.created_at = datetime.now(UTC)
    mock_log.user_id = None
    mock_log.entity_type = None
    mock_log.entity_id = None
    mock_log.details = None
    mock_log.ip_address = None
    mock_log.user_agent = None

    async def async_gen():
        yield mock_log

    mock_stream_result = MagicMock()
    mock_stream_result.scalars.return_value = async_gen()
    mock_ctx.db.stream = AsyncMock(return_value=mock_stream_result)

    result = await export_audit_logs(
        ctx=mock_ctx,
        format="jsonl",
        action=None,
        user_id=None,
        entity_type=None,
        start_date=None,
        end_date=None,
    )

    assert result.media_type == "application/x-ndjson"
