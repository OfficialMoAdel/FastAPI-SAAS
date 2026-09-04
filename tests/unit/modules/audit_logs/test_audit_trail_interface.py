"""Unit tests for the deep audit-trail interface (trail.py, schemas, and event handlers)."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.app.core.events import EventPriority
from src.app.core.tenancy.context import TenantContext, set_current_tenant
from src.app.modules.audit_logs.schemas import AuditLogEventData
from src.app.modules.audit_logs.service import handle_audit_log_event
from src.app.modules.audit_logs.trail import SYSTEM_TENANT_ID, _coerce_user_id, _resolve_tenant_id, audit


class TestAuditTrailInterface:
    """Test suite for the typed audit() interface and resolution helpers."""

    def test_system_tenant_id_constant(self) -> None:
        """Verify SYSTEM_TENANT_ID is defined as 00000000-0000-0000-0000-000000000000."""
        assert SYSTEM_TENANT_ID == UUID(int=0)
        assert str(SYSTEM_TENANT_ID) == "00000000-0000-0000-0000-000000000000"

    def test_resolve_tenant_id_explicit(self) -> None:
        """Verify explicit tenant_id resolution with UUID, string, int, and '0'."""
        test_uuid = UUID("11111111-1111-1111-1111-111111111111")
        assert _resolve_tenant_id(test_uuid) == test_uuid
        assert _resolve_tenant_id("11111111-1111-1111-1111-111111111111") == test_uuid
        assert _resolve_tenant_id("0") == SYSTEM_TENANT_ID
        assert _resolve_tenant_id("") == SYSTEM_TENANT_ID
        assert _resolve_tenant_id(0) == SYSTEM_TENANT_ID
        assert _resolve_tenant_id("invalid-uuid-string") == SYSTEM_TENANT_ID

    def test_resolve_tenant_id_from_context(self) -> None:
        """Verify tenant_id resolution from TenantContext when not explicitly passed."""
        context_tenant_id = UUID("22222222-2222-2222-2222-222222222222")
        tenant_ctx = TenantContext(
            tenant_id=context_tenant_id,
            tenant_slug="test-org",
            tenant_name="Test Org",
            is_active=True,
        )
        set_current_tenant(tenant_ctx)
        try:
            assert _resolve_tenant_id(None) == context_tenant_id
        finally:
            set_current_tenant(None)

    def test_resolve_tenant_id_fallback_to_system(self) -> None:
        """Verify fallback to SYSTEM_TENANT_ID when no context exists."""
        set_current_tenant(None)
        assert _resolve_tenant_id(None) == SYSTEM_TENANT_ID

    def test_coerce_user_id(self) -> None:
        """Verify user_id coercion for int, str, dict, and objects."""
        assert _coerce_user_id(None) is None
        assert _coerce_user_id(42) == "42"
        assert _coerce_user_id("99") == "99"
        assert _coerce_user_id("  100  ") == "100"
        assert _coerce_user_id("") is None
        assert _coerce_user_id({"id": 123, "username": "alice"}) == "123"

        class DummyUser:
            id = 456

        assert _coerce_user_id(DummyUser()) == "456"

    def test_audit_log_event_data_rejects_malformed_tenant_uuid(self) -> None:
        """AuditLogEventData raises ValueError when tenant_id is an invalid UUID string."""
        with pytest.raises(ValueError, match="Invalid UUID string for tenant_id"):
            AuditLogEventData(
                action="test.action",
                entity_type="user",
                entity_id=1,
                tenant_id="not-a-valid-uuid",
            )

    @pytest.mark.asyncio
    async def test_audit_publishes_valid_event(self) -> None:
        """Verify audit() publishes validated payload to event_bus."""
        tenant_uuid = UUID("33333333-3333-3333-3333-333333333333")
        with patch("src.app.modules.audit_logs.trail.event_bus.publish", new_callable=AsyncMock) as mock_pub:
            await audit(
                action="user.login",
                entity_type="user",
                entity_id="123",
                tenant_id=tenant_uuid,
                user_id=123,
                details={"username": "testuser", "ip": "1.2.3.4"},
                ip_address="1.2.3.4",
                user_agent="Mozilla/5.0",
                priority=EventPriority.CRITICAL,
            )

            mock_pub.assert_called_once()
            args, kwargs = mock_pub.call_args
            assert args[0] == "audit.log"
            payload = args[1]
            assert payload["action"] == "user.login"
            assert payload["entity_type"] == "user"
            assert kwargs.get("priority") == EventPriority.CRITICAL
            assert payload["user_id"] == "123"
            assert payload["details"] == {"username": "testuser", "ip": "1.2.3.4"}
            assert payload["ip_address"] == "1.2.3.4"
            assert payload["user_agent"] == "Mozilla/5.0"

    @pytest.mark.asyncio
    async def test_audit_redacts_sensitive_details(self) -> None:
        """Verify audit() automatically sanitizes sensitive fields in details."""
        with patch("src.app.modules.audit_logs.trail.event_bus.publish", new_callable=AsyncMock) as mock_pub:
            await audit(
                action="auth.password_changed",
                entity_type="user",
                entity_id=1,
                details={
                    "password_entry": "password=MySecretPassword123!",
                    "bearer_token": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.xyz",
                    "safe_field": "public_data",
                },
            )

            mock_pub.assert_called_once()
            payload = mock_pub.call_args[0][1]
            details = payload["details"]
            assert details["safe_field"] == "public_data"
            # Sensitive tokens/passwords must be sanitized
            assert "MySecretPassword123!" not in details["password_entry"]
            assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.xyz" not in details["bearer_token"]
    @pytest.mark.asyncio
    async def test_audit_alias_normalization(self) -> None:
        """Verify audit() normalizes resource_type/resource_id aliases."""
        with patch("src.app.modules.audit_logs.trail.event_bus.publish", new_callable=AsyncMock) as mock_pub:
            await audit(
                action="system.rls_bypass",
                resource_type="system",
                resource_id="rls_context",
                details={"reason": "Cron job execution"},
            )

            mock_pub.assert_called_once()
            payload = mock_pub.call_args[0][1]
            assert payload["action"] == "system.rls_bypass"
            assert payload["entity_type"] == "system"
            assert payload["entity_id"] == "rls_context"
            assert payload["tenant_id"] == str(SYSTEM_TENANT_ID)
            assert payload["details"] == {"reason": "Cron job execution"}

    @pytest.mark.asyncio
    async def test_audit_handles_publish_failure_fail_open(self) -> None:
        """Verify audit() catches event_bus errors when AUDIT_LOG_FAILURE_MODE='fail_open'."""
        with (
            patch("src.app.modules.audit_logs.trail.event_bus.publish", new_callable=AsyncMock) as mock_pub,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", "fail_open"),
        ):
            mock_pub.side_effect = RuntimeError("Event bus unreachable")
            # Should not raise exception in fail_open mode
            await audit(
                action="test.action",
                details={"key": "val"},
            )

    @pytest.mark.asyncio
    async def test_audit_handles_publish_failure_fail_closed(self) -> None:
        """Verify audit() re-raises event_bus errors when AUDIT_LOG_FAILURE_MODE='fail_closed'."""
        with (
            patch("src.app.modules.audit_logs.trail.event_bus.publish", new_callable=AsyncMock) as mock_pub,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", "fail_closed"),
        ):
            mock_pub.side_effect = RuntimeError("Event bus unreachable")
            with pytest.raises(RuntimeError, match="Event bus unreachable"):
                await audit(
                    action="test.action",
                    details={"key": "val"},
                )


class TestAuditLogEventDataSchema:
    """Test suite for AuditLogEventData Pydantic schema validation."""

    def test_schema_valid_payload(self) -> None:
        """Validate well-formed AuditLogEventData."""
        data = AuditLogEventData(
            tenant_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            action="post.created",
            entity_type="post",
            entity_id="101",
            user_id="5",
            details={"title": "Hello World"},
        )
        assert data.action == "post.created"
        assert data.entity_type == "post"
        assert data.entity_id == "101"
        assert str(data.tenant_id) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert data.user_id == "5"

    def test_schema_normalizes_aliases(self) -> None:
        """Validate resource_type and resource_id alias normalization."""
        data = AuditLogEventData.model_validate(
            {
                "action": "file.uploaded",
                "resource_type": "file",
                "resource_id": 999,
                "tenant_id": "0",
                "user_id": 12,
            }
        )
        assert data.entity_type == "file"
        assert data.entity_id == "999"
        assert data.user_id == "12"
        assert data.tenant_id == SYSTEM_TENANT_ID

    def test_schema_handles_none_tenant(self) -> None:
        """Validate that missing or empty tenant_id converts to SYSTEM_TENANT_ID."""
        data = AuditLogEventData.model_validate(
            {
                "action": "system.maintenance",
                "tenant_id": None,
            }
        )
        assert data.tenant_id == SYSTEM_TENANT_ID


class TestHandleAuditLogEvent:
    """Test suite for handle_audit_log_event bus event handler."""

    @pytest.mark.asyncio
    async def test_handle_event_success_buffers_in_redis(self) -> None:
        """Verify valid event is buffered via buffer_audit_log."""
        event_dict = {
            "action": "auth.login",
            "entity_type": "user",
            "entity_id": "1",
            "tenant_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "user_id": "1",
            "details": {"success": True},
        }

        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer:
            await handle_audit_log_event(event_dict)

            mock_buffer.assert_called_once()
            buffered = mock_buffer.call_args[0][0]
            assert buffered["action"] == "auth.login"
            assert buffered["tenant_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            assert buffered["entity_type"] == "user"
            assert buffered["entity_id"] == "1"
            assert "created_at" in buffered

    @pytest.mark.asyncio
    async def test_handle_event_with_legacy_keys_and_missing_tenant_no_key_error(self) -> None:
        """CRITICAL REGRESSION TEST: Verified live bug in plan where rls_context published with

        resource_type/resource_id and NO tenant_id, causing KeyError in handle_audit_log_event.
        """
        legacy_event_dict = {
            "action": "system.rls_bypass",
            "resource_type": "system",
            "resource_id": "rls_context",
            "details": {"reason": "Background migration"},
        }

        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer:
            # Must NOT raise KeyError: 'tenant_id'
            await handle_audit_log_event(legacy_event_dict)

            mock_buffer.assert_called_once()
            buffered = mock_buffer.call_args[0][0]
            assert buffered["action"] == "system.rls_bypass"
            assert buffered["entity_type"] == "system"
            assert buffered["entity_id"] == "rls_context"
            assert buffered["tenant_id"] == str(SYSTEM_TENANT_ID)
            assert buffered["details"] == {"reason": "Background migration"}
