"""Unit tests for P3 Code Quality Regressions (P3-1 to P3-4)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.deps.auth import AuthenticatedUser
from src.app.modules.ai.router import chat_completion
from src.app.modules.ai.service import ChatCompletionError
from src.app.modules.audit_logs.service import AuditLogService
from src.app.modules.auth.crud import crud_passkey_credentials, crud_user_sso_connections
from src.app.modules.notifications.service import NotificationService


class TestP3CodeQualityRegressions:
    """Regression test suite for P3 Code Quality fixes."""

    @pytest.mark.asyncio
    async def test_p3_1_audit_log_service_get_log(self):
        """P3-1: AuditLogService.get_log executes tenant-scoped query."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_log = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_log
        mock_db.execute.return_value = mock_result

        service = AuditLogService(mock_db)
        log_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        result = await service.get_log(log_id=log_id, tenant_id=tenant_id)
        assert result is mock_log
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_p3_1_notification_service_get_notification(self):
        """P3-1: NotificationService.get_notification executes tenant- and user-scoped query."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_notification = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_notification
        mock_db.execute.return_value = mock_result

        service = NotificationService(mock_db)
        notification_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        result = await service.get_notification(
            notification_id=notification_id,
            tenant_id=tenant_id,
            user_id=123,
        )
        assert result is mock_notification
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_p3_2_passkey_crud_deletes_credential(self):
        """P3-2: CRUDPasskeyCredential.delete_credential executes deletion."""
        mock_db = AsyncMock()
        mock_cred = MagicMock()

        await crud_passkey_credentials.delete_credential(db=mock_db, db_object=mock_cred)
        mock_db.delete.assert_called_once_with(mock_cred)
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_p3_2_sso_crud_deletes_connection(self):
        """P3-2: CRUDUserSSOConnection.delete_by_criteria executes deletion."""
        mock_db = AsyncMock()
        mock_conn = MagicMock()

        with patch.object(crud_user_sso_connections, "get_by_criteria", new_callable=AsyncMock, return_value=mock_conn):
            await crud_user_sso_connections.delete_by_criteria(db=mock_db, tenant_id=uuid.uuid4(), user_id=1)
            mock_db.delete.assert_called_once_with(mock_conn)
            mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_p3_3_ai_router_error_envelope_format(self):
        """P3-3: chat_completion raises HTTPException with flat detail string on ChatCompletionError."""
        mock_db = AsyncMock()
        mock_service = AsyncMock()
        mock_service.complete.side_effect = ChatCompletionError(
            message="Provider service unavailable",
            model="test-model",
            error_code="SERVICE_UNAVAILABLE",
        )

        with patch("src.app.modules.ai.router.get_chat_completion_service", return_value=mock_service):
            with pytest.raises(HTTPException) as exc_info:
                await chat_completion(
                    session_id=1,
                    request=MagicMock(),
                    current_user=AuthenticatedUser(id=1, email="u@e.com"),
                    db=mock_db,
                )
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail == "Provider service unavailable"
