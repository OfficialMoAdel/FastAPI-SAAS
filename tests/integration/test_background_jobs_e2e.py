import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from src.app.core.worker.functions import flush_audit_logs_task, deliver_webhook_task


@pytest.mark.asyncio
async def test_flush_audit_logs_task_uses_rls_bypass():
    """Verify flush_audit_logs_task passes the RLS-bypass session to the service."""
    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None

    # We mock job_bypass_context to return our mock session
    with patch("src.app.core.worker.functions.job_bypass_context", return_value=mock_cm):
        # We mock flush_audit_log_buffer in the SERVICE module because it's imported locally
        # Use side_effect to return 10 once, then 0 to stop the loop
        with patch("src.app.modules.audit_logs.service.flush_audit_log_buffer", new=AsyncMock(side_effect=[10, 0])) as mock_flush:

            result = await flush_audit_logs_task({})

            assert "Flushed 10 audit logs" in result

            # Verify flush_audit_log_buffer called with db=mock_session
            # Called twice (once for 10, once for 0)
            assert mock_flush.call_count == 2
            call_kwargs = mock_flush.call_args.kwargs
            assert call_kwargs.get("db") is mock_session


@pytest.mark.asyncio
async def test_deliver_webhook_task_uses_rls_context():
    """Verify deliver_webhook_task passes the RLS session to WebhookService."""
    mock_session = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None

    # Mock job_bypass_context for the first part (fetching tenant_id)
    mock_bypass_session = AsyncMock()
    # Configure execute result to have synchronous fetchone
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (123,)  # tenant_id
    mock_bypass_session.execute.return_value = mock_result

    mock_bypass_cm = MagicMock()
    mock_bypass_cm.__aenter__.return_value = mock_bypass_session
    mock_bypass_cm.__aexit__.return_value = None

    with (
        patch("src.app.core.worker.functions.job_bypass_context", return_value=mock_bypass_cm),
        patch("src.app.core.worker.functions.job_tenant_context", return_value=mock_cm) as mock_tenant_ctx_factory,
    ):
        with patch("src.app.core.worker.functions.WebhookService") as MockService:
            mock_service_instance = MockService.return_value
            mock_service_instance.deliver_webhook = AsyncMock()

            await deliver_webhook_task(MagicMock(), "d290f1ee-6c54-4b01-90e6-d701748f0851")

            # Verify job_tenant_context called with correct tenant_id
            mock_tenant_ctx_factory.assert_called_once_with(123)

            # Verify WebhookService initialized with the session from job_tenant_context
            MockService.assert_called_once_with(mock_session)

            # Verify delivery method called
            mock_service_instance.deliver_webhook.assert_called_once()