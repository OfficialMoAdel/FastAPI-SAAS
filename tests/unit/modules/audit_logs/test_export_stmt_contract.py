"""B3: export SQL stays inside the service's lazy streaming operation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Select

from src.app.core.tenancy.context import TenantContext, get_current_tenant, set_current_tenant
from src.app.modules.audit_logs import router as audit_router
from src.app.modules.audit_logs import service as audit_service
from src.app.modules.audit_logs.schemas import AuditExportCriteria


class TestExportStmtContract:
    def test_router_does_not_import_private_compose_filters(self) -> None:
        assert not hasattr(audit_router, "_compose_filters")

    def test_service_exposes_stream_export(self) -> None:
        assert callable(getattr(audit_service.AuditLogService, "stream_export", None))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filtered", [False, True])
    async def test_stream_export_applies_matching_count_and_stream_filters(self, filtered: bool) -> None:
        tenant_id = UUID("11111111-1111-1111-1111-111111111111")
        previous = get_current_tenant()
        set_current_tenant(
            TenantContext(tenant_id=tenant_id, tenant_slug="test-org", tenant_name="Test Org", is_active=True)
        )
        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 2
        db.execute.return_value = count_result
        result = MagicMock()
        result.close = AsyncMock()
        db.stream.return_value = result
        svc = audit_service.AuditLogService(db)
        event_id = uuid4()
        svc.create_log = AsyncMock(return_value=SimpleNamespace(id=event_id))
        criteria = AuditExportCriteria(
            tenant_id=tenant_id,
            action="user.created" if filtered else None,
            user_id=42 if filtered else None,
            entity_type="user" if filtered else None,
            start_date=datetime(2026, 1, 1, tzinfo=UTC) if filtered else None,
            end_date=datetime(2026, 2, 1, tzinfo=UTC) if filtered else None,
        )
        try:
            chunks = await svc.stream_export(criteria, actor=7)
            db.execute.assert_awaited_once()
            db.stream.assert_not_awaited()
            try:
                await anext(chunks)  # CSV header opens the cursor, without fetching rows.
            finally:
                await chunks.aclose()
        finally:
            set_current_tenant(previous)

        count_stmt = db.execute.await_args.args[0]
        stmt = db.stream.await_args.args[0]
        assert isinstance(stmt, Select)
        predicates = [audit_service.AuditLog.tenant_id == tenant_id]
        if filtered:
            predicates.extend([
                audit_service.AuditLog.action == criteria.action,
                audit_service.AuditLog.user_id == 42,
                audit_service.AuditLog.entity_type == criteria.entity_type,
                audit_service.AuditLog.created_at >= criteria.start_date,
                audit_service.AuditLog.created_at <= criteria.end_date,
            ])
        # SQL expression comparison includes bound values, not only column names.
        from sqlalchemy import and_

        assert count_stmt.whereclause.compare(and_(*predicates))
        assert stmt.whereclause.compare(and_(*predicates, audit_service.AuditLog.id != event_id))
        assert "ORDER BY audit_logs.created_at ASC, audit_logs.id ASC" in str(stmt)
        assert stmt.compile().params["param_1"] == 100_000
        assert stmt.get_execution_options()["yield_per"] == 1000
        svc.create_log.assert_awaited_once_with(
            tenant_id=tenant_id, user_id=7, action="audit_logs.export", entity_type="audit_log",
            entity_id=None, details={"format": "csv", "record_count": 2},
        )
        result.close.assert_awaited_once_with()
