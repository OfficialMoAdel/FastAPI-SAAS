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

from src.app.modules.audit_logs.router import (
    export_audit_logs,
    get_audit_log,
    get_export_count,
    list_audit_logs,
    list_available_actions,
)
from src.app.modules.audit_logs.service import AuditFilter
from src.app.shared.exceptions import ModelNotFoundError, ValidationError


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

    with pytest.raises(ModelNotFoundError, match="Audit log"):
        await get_audit_log(log_id=uuid4(), ctx=mock_ctx)


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

    # The count query goes through the module's public filter value object.
    tenant_id, audit_filter = mock_service.export_count.await_args.args
    assert type(audit_filter) is AuditFilter
    assert audit_filter == AuditFilter(
        action="user.login", user_id=1, entity_type="user", start_date=None, end_date=None
    )
    assert tenant_id == mock_ctx.tenant_id

    assert result.count == 50
    assert result.can_export is True


def test_router_binds_no_private_name_from_its_own_module() -> None:
    """The edge must not reach into the module's private surface.

    Anything the router needs from the audit-logs module has to be public, or
    a worker and the next caller are free to move it.
    """
    from src.app.modules.audit_logs import router as audit_router
    from src.app.modules.audit_logs import service as audit_service

    leaked = sorted(
        name
        for name in vars(audit_router)
        if name.startswith("_") and not name.startswith("__") and name in vars(audit_service)
    )
    assert leaked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [ValidationError("Export exceeds maximum"), RuntimeError("audit write failed")],
)
@patch("src.app.modules.audit_logs.router.StreamingResponse")
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_errors_propagate_before_response(mock_service_cls, response_cls, mock_ctx, error) -> None:
    mock_service = mock_service_cls.return_value
    mock_service.stream_export = AsyncMock(side_effect=error)

    with pytest.raises(type(error)) as caught:
        await export_audit_logs(
            ctx=mock_ctx,
            format="csv",
            action=None,
            user_id=None,
            entity_type=None,
            start_date=None,
            end_date=None,
        )

    assert caught.value is error
    mock_service.stream_export.assert_awaited_once()
    response_cls.assert_not_called()
    mock_ctx.db.execute.assert_not_awaited()
    mock_ctx.db.stream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("export_format", "content_type", "payload"),
    [
        ("csv", "text/csv; charset=utf-8", [b"action\r\n", b"test.action\r\n"]),
        ("json", "application/json", [b"[", b'{"action":"test.action"}', b"]"]),
        ("jsonl", "application/x-ndjson", [b'{"action":"test.action"}\n', b"{}\n"]),
    ],
)
@pytest.mark.parametrize("filtered", [False, True])
@patch("src.app.modules.audit_logs.router.AuditLogService")
async def test_export_delegates_criteria_and_streams_unchanged(
    mock_service_cls, mock_ctx, export_format, content_type, payload, filtered
) -> None:
    from src.app.modules.audit_logs.schemas import AuditExportCriteria

    async def chunks():
        for chunk in payload:
            yield chunk

    stream = chunks()
    mock_service = mock_service_cls.return_value
    mock_service.stream_export = AsyncMock(return_value=stream)
    filters = {
        "action": "test.action" if filtered else None,
        "user_id": 42 if filtered else None,
        "entity_type": "test" if filtered else None,
        "start_date": datetime(2026, 1, 1, tzinfo=UTC) if filtered else None,
        "end_date": datetime(2026, 2, 1, tzinfo=UTC) if filtered else None,
    }

    result = await export_audit_logs(ctx=mock_ctx, format=export_format, **filters)

    mock_service_cls.assert_called_once_with(mock_ctx.db)
    mock_service.stream_export.assert_awaited_once_with(
        AuditExportCriteria(tenant_id=mock_ctx.tenant_id, format=export_format, **filters),
        actor=mock_ctx.user_id,
    )
    assert result.status_code == 200
    assert result.headers["content-type"] == content_type
    assert result.headers["content-disposition"] == f"attachment; filename=audit_logs.{export_format}"
    assert result.body_iterator is stream
    assert [chunk async for chunk in result.body_iterator] == payload
    mock_service.export_count.assert_not_called()
    mock_service.create_log.assert_not_called()
    mock_ctx.db.execute.assert_not_awaited()
    mock_ctx.db.stream.assert_not_awaited()
