"""P2-2: export query assembly lives in the service, not the router."""

from unittest.mock import MagicMock
from uuid import UUID

from sqlalchemy import Select

from src.app.core.tenancy.context import TenantContext, set_current_tenant
from src.app.modules.audit_logs import router as audit_router
from src.app.modules.audit_logs import service as audit_service


class TestExportStmtContract:
    """Router delegates export filtering to AuditLogService.export_stmt."""

    def test_router_does_not_import_private_compose_filters(self) -> None:
        assert not hasattr(audit_router, "_compose_filters")

    def test_service_exposes_export_stmt(self) -> None:
        assert callable(getattr(audit_service.AuditLogService, "export_stmt", None))

    def test_export_stmt_applies_tenant_scope_and_filters(self) -> None:
        tenant_id = UUID("11111111-1111-1111-1111-111111111111")
        set_current_tenant(
            TenantContext(
                tenant_id=tenant_id,
                tenant_slug="test-org",
                tenant_name="Test Org",
                is_active=True,
            )
        )
        try:
            svc = audit_service.AuditLogService(MagicMock())
            f = audit_service._AuditFilter(
                action="user.created",
                user_id=None,
                entity_type=None,
                start_date=None,
                end_date=None,
            )
            stmt = svc.export_stmt(tenant_id, f)
        finally:
            set_current_tenant(None)
        assert isinstance(stmt, Select)
        # tenant_scope + action filter = 2 WHERE criteria.
        assert stmt.whereclause is not None
        assert len(stmt.whereclause.clauses) == 2
        params = list(stmt.compile().params.values())
        assert tenant_id in params
        assert "user.created" in params
