"""Unit tests for the deep audit-trail interface (trail.py, schemas, and event handlers)."""

import asyncio
from contextlib import nullcontext
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from src.app.core.tenancy.context import TenantContext, set_current_tenant
from src.app.modules.audit_logs.schemas import AuditLogEventData
from src.app.modules.audit_logs.service import handle_audit_log_event
from src.app.modules.audit_logs.trail import (
    SYSTEM_TENANT_ID,
    AuditGuarantee,
    _coerce_user_id,
    _resolve_tenant_id,
    audit,
)


class TestAuditTrailInterface:
    """Test suite for the typed audit() interface and resolution helpers."""

    def test_system_tenant_id_constant(self) -> None:
        """Verify SYSTEM_TENANT_ID is defined as 00000000-0000-0000-0000-000000000000."""
        assert SYSTEM_TENANT_ID == UUID(int=0)
        assert str(SYSTEM_TENANT_ID) == "00000000-0000-0000-0000-000000000000"

    def test_resolve_tenant_id_explicit(self) -> None:
        """P2-C: UUID is the sole tenant type — explicit UUID passes through."""
        test_uuid = UUID("11111111-1111-1111-1111-111111111111")
        assert _resolve_tenant_id(test_uuid) == test_uuid

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

    def test_system_tenant_id_constructor_logs_reason(self) -> None:
        """A1: the SYSTEM fallback is a named, logged constructor (not a twin)."""
        from src.app.modules.audit_logs.trail import system_tenant_id

        with patch("src.app.modules.audit_logs.trail.logger") as mock_logger:
            assert system_tenant_id(reason="audit.test_reason") == SYSTEM_TENANT_ID
            mock_logger.warning.assert_called_once_with(
                "audit_tenant_fallback_to_system", action="audit.test_reason"
            )

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
                entity_id="1",
                tenant_id="not-a-valid-uuid",
            )

    @pytest.mark.asyncio
    async def test_audit_delivers_valid_payload(self) -> None:
        """Verify audit() validates, stamps, and delivers payload to the writer."""
        tenant_uuid = UUID("33333333-3333-3333-3333-333333333333")
        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer:
            await audit(
                action="user.login",
                entity_type="user",
                entity_id="123",
                tenant_id=tenant_uuid,
                user_id=123,
                details={"username": "testuser", "ip": "1.2.3.4"},
                ip_address="1.2.3.4",
                user_agent="Mozilla/5.0",
            )

            mock_buffer.assert_called_once()
            payload = mock_buffer.call_args[0][0]
            assert payload["action"] == "user.login"
            assert payload["entity_type"] == "user"
            assert payload["entity_id"] == "123"
            assert payload["tenant_id"] == str(tenant_uuid)
            assert payload["user_id"] == "123"
            assert payload["details"] == {"username": "testuser", "ip": "1.2.3.4"}
            assert payload["ip_address"] == "1.2.3.4"
            assert payload["user_agent"] == "Mozilla/5.0"
            assert "created_at" in payload

    @pytest.mark.asyncio
    async def test_audit_redacts_sensitive_details(self) -> None:
        """Verify audit() automatically sanitizes sensitive fields in details."""
        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer:
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

            mock_buffer.assert_called_once()
            payload = mock_buffer.call_args[0][0]
            details = payload["details"]
            assert details["safe_field"] == "public_data"
            # Sensitive tokens/passwords must be sanitized
            assert "MySecretPassword123!" not in details["password_entry"]
            assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.xyz" not in details["bearer_token"]

    @pytest.mark.asyncio
    async def test_audit_alias_normalization(self) -> None:
        """Verify audit() normalizes resource_type/resource_id aliases."""
        with patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer:
            await audit(
                action="system.rls_bypass",
                resource_type="system",
                resource_id="rls_context",
                details={"reason": "Cron job execution"},
            )

            mock_buffer.assert_called_once()
            payload = mock_buffer.call_args[0][0]
            assert payload["action"] == "system.rls_bypass"
            assert payload["entity_type"] == "system"
            assert payload["entity_id"] == "rls_context"
            assert payload["tenant_id"] == str(SYSTEM_TENANT_ID)
            assert payload["details"] == {"reason": "Cron job execution", "_tenant_fallback": "system"}

    @pytest.mark.asyncio
    async def test_audit_delivery_failure_best_effort_swallows(self) -> None:
        """Verify audit() logs and swallows writer errors when the mode maps to BEST_EFFORT."""
        with (
            patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", "fail_open"),
        ):
            mock_buffer.side_effect = RuntimeError("Redis unreachable")
            # Should not raise in best-effort mode
            await audit(
                action="test.action",
                details={"key": "val"},
            )

    @pytest.mark.asyncio
    async def test_audit_delivery_failure_required_re_raises(self) -> None:
        """Verify audit() re-raises writer errors when the mode maps to REQUIRED."""
        with (
            patch("src.app.modules.audit_logs.service.buffer_audit_log", new_callable=AsyncMock) as mock_buffer,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", "fail_closed"),
        ):
            mock_buffer.side_effect = RuntimeError("Redis unreachable")
            with pytest.raises(RuntimeError, match="Redis unreachable"):
                await audit(
                    action="test.action",
                    details={"key": "val"},
                )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("guarantee", "configured_mode"),
        [
            (AuditGuarantee.BEST_EFFORT, "fail_closed"),
            (AuditGuarantee.REQUIRED, "fail_open"),
        ],
    )
    async def test_audit_explicit_guarantee_overrides_config(
        self, guarantee: AuditGuarantee, configured_mode: str
    ) -> None:
        error = RuntimeError("private writer payload")
        tenant_id = UUID("33333333-3333-3333-3333-333333333333")
        with (
            patch(
                "src.app.modules.audit_logs.service.buffer_audit_log",
                new_callable=AsyncMock,
                side_effect=error,
            ) as writer,
            patch("src.app.core.config.settings.auth.AUDIT_LOG_FAILURE_MODE", configured_mode),
            patch("src.app.modules.audit_logs.trail.logger") as logger,
        ):
            expectation = pytest.raises(RuntimeError) if guarantee == AuditGuarantee.REQUIRED else nullcontext()
            with expectation as exc_info:
                await audit(action="test.action", tenant_id=tenant_id, guarantee=guarantee)

            if guarantee == AuditGuarantee.REQUIRED:
                assert exc_info is not None
                assert exc_info.value is error
            writer.assert_awaited_once()
            logger.warning.assert_called_once_with(
                "audit_delivery_failed", action="test.action", guarantee=guarantee.value, error_type="RuntimeError"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("guarantee", list(AuditGuarantee))
    async def test_audit_awaits_writer_and_propagates_cancellation(self, guarantee: AuditGuarantee) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked_writer(payload: dict[str, Any]) -> None:
            entered.set()
            await release.wait()

        with (
            patch("src.app.modules.audit_logs.service.buffer_audit_log", side_effect=blocked_writer) as writer,
            patch("src.app.modules.audit_logs.trail.logger") as logger,
        ):
            task = asyncio.create_task(
                audit(action="test.action", tenant_id=UUID(int=1), guarantee=guarantee)
            )
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                assert not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                writer.assert_awaited_once()
                logger.warning.assert_not_called()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)



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
            assert buffered["details"] == {"reason": "Background migration", "_tenant_fallback": "system"}
