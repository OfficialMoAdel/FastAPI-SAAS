"""Unit tests for consolidated migration layers (01-08) and model index parity."""

import glob
import importlib
from unittest.mock import MagicMock, patch

from sqlalchemy import Index

from src.app.modules.audit_logs.models import AuditLog
from src.app.modules.notifications.models import Notification
from src.app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint


class TestP0IndexesAndParity:
    """Test suite verifying consolidated 8-layer migrations, P0 index fixes, and model parity."""

    def test_audit_log_model_declared_indexes(self) -> None:
        """P0-2: Verify AuditLog declares idx_audit_tenant_action."""
        index_map = {idx.name: [c.name for c in idx.columns] for idx in AuditLog.__table__.indexes}

        assert "idx_audit_tenant_action" in index_map
        assert index_map["idx_audit_tenant_action"] == ["tenant_id", "action"]
        assert "idx_audit_tenant_created" in index_map
        assert "idx_audit_entity" in index_map
        assert "idx_audit_action_created" in index_map

    def test_webhook_delivery_model_declared_indexes(self) -> None:
        """P0-3: Verify WebhookDelivery declares idx_delivery_tenant_status_created and partial indexes."""
        index_map = {idx.name: [c.name for c in idx.columns] for idx in WebhookDelivery.__table__.indexes}

        assert "idx_delivery_tenant_status_created" in index_map
        assert index_map["idx_delivery_tenant_status_created"] == ["tenant_id", "status", "created_at"]
        assert "idx_webhook_delivery_retry" in index_map
        assert "idx_webhook_delivery_endpoint_status" in index_map
        assert "idx_webhook_delivery_created" in index_map

    def test_webhook_endpoint_model_declared_indexes(self) -> None:
        """Verify WebhookEndpoint declares composite indexes."""
        index_map = {idx.name: [c.name for c in idx.columns] for idx in WebhookEndpoint.__table__.indexes}

        assert "idx_webhook_endpoint_tenant_event" in index_map
        assert index_map["idx_webhook_endpoint_tenant_event"] == ["tenant_id", "event_type", "is_active"]
        assert "idx_webhook_endpoint_active" in index_map

    def test_notification_model_declared_indexes(self) -> None:
        """Verify Notification declares composite and partial indexes."""
        index_map = {idx.name: [c.name for c in idx.columns] for idx in Notification.__table__.indexes}

        assert "idx_notification_status_created" in index_map
        assert index_map["idx_notification_status_created"] == ["status", "created_at"]
        assert "idx_notification_tenant_status" in index_map
        assert "idx_notification_tenant_created" in index_map

    def test_migration_chain_integrity(self) -> None:
        """Verify 8 migration layers form an unbroken chain from 01_foundation to 08_enable_rls_policies."""
        expected_chain = [
            ("01_foundation", None),
            ("02_authentication", "01_foundation"),
            ("03_access_control", "02_authentication"),
            ("04_billing", "03_access_control"),
            ("05_content_storage", "04_billing"),
            ("06_ai_features", "05_content_storage"),
            ("07_observability", "06_ai_features"),
            ("08_enable_rls_policies", "07_observability"),
        ]

        for rev, down_rev in expected_chain:
            mod = importlib.import_module(f"src.migrations.versions.{rev}")
            assert mod.revision == rev
            assert mod.down_revision == down_rev

    def test_01_foundation_system_tenant_seed(self) -> None:
        """Verify 01_foundation seeds the system tenant row."""
        mig_01 = importlib.import_module("src.migrations.versions.01_foundation")

        mock_op = MagicMock()
        with patch.object(mig_01, "op", mock_op):
            mig_01.upgrade()

            executed_sqls = [call.args[0] for call in mock_op.execute.call_args_list if call.args]
            assert any("00000000-0000-0000-0000-000000000000" in sql for sql in executed_sqls)
            assert any("ON CONFLICT (id) DO NOTHING" in sql for sql in executed_sqls)

    def test_07_observability_indexes(self) -> None:
        """Verify 07_observability upgrade() creates all P0 and model-parity indexes."""
        mig_07 = importlib.import_module("src.migrations.versions.07_observability")

        mock_op = MagicMock()
        with patch.object(mig_07, "op", mock_op):
            mig_07.upgrade()

            created_indexes = [(call.args, call.kwargs) for call in mock_op.create_index.call_args_list]

            # P0-2 index
            assert any(
                args[0] == "idx_audit_tenant_action"
                and args[1] == "audit_logs"
                and args[2] == ["tenant_id", "action"]
                for args, _ in created_indexes
            )

            # P0-3 index with DESC ordering
            assert any(
                args[0] == "idx_delivery_tenant_status_created"
                and args[1] == "webhook_deliveries"
                and args[2] == ["tenant_id", "status", "created_at"]
                and kwargs.get("postgresql_ops") == {"created_at": "DESC"}
                for args, kwargs in created_indexes
            )

            # Webhook delivery partial retry index
            assert any(
                args[0] == "idx_webhook_delivery_retry"
                and args[1] == "webhook_deliveries"
                and args[2] == ["status", "next_retry_at"]
                and "postgresql_where" in kwargs
                for args, kwargs in created_indexes
            )

            # Webhook delivery endpoint+status composite
            assert any(
                args[0] == "idx_webhook_delivery_endpoint_status"
                and args[1] == "webhook_deliveries"
                and args[2] == ["webhook_endpoint_id", "status"]
                for args, _ in created_indexes
            )

            # Webhook delivery created DESC index
            assert any(
                args[0] == "idx_webhook_delivery_created"
                and args[1] == "webhook_deliveries"
                and args[2] == ["created_at"]
                and kwargs.get("postgresql_ops") == {"created_at": "DESC"}
                for args, kwargs in created_indexes
            )

            # Audit action created index
            assert any(
                args[0] == "idx_audit_action_created"
                and args[1] == "audit_logs"
                and args[2] == ["action", "created_at"]
                for args, _ in created_indexes
            )

            # Webhook endpoint tenant event index
            assert any(
                args[0] == "idx_webhook_endpoint_tenant_event"
                and args[1] == "webhook_endpoints"
                and args[2] == ["tenant_id", "event_type", "is_active"]
                for args, _ in created_indexes
            )

            # Notification failed status partial index
            assert any(
                args[0] == "idx_notification_status_created"
                and args[1] == "notifications"
                and args[2] == ["status", "created_at"]
                and "postgresql_where" in kwargs
                for args, kwargs in created_indexes
            )

    def test_all_model_indexes_covered_by_migrations(self) -> None:
        """Verify all declared model indexes in __table_args__ appear in Alembic migration files."""
        # Gather all migration contents
        migration_files = glob.glob("src/migrations/versions/*.py")
        migration_content = ""
        for mf in migration_files:
            with open(mf, encoding="utf-8") as f:
                migration_content += f.read() + "\n"

        # Collect all explicitly declared index names in __table_args__
        target_models = [AuditLog, WebhookDelivery, WebhookEndpoint, Notification]
        declared_index_names = set()
        for model in target_models:
            if hasattr(model, "__table_args__"):
                for arg in model.__table_args__:
                    if isinstance(arg, Index):
                        declared_index_names.add(arg.name)

        # Assert that every explicitly declared index name appears in the migrations
        missing = [name for name in declared_index_names if name not in migration_content]
        assert not missing, f"Model __table_args__ indexes missing from migration files: {missing}"
