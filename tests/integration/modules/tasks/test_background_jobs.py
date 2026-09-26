from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.worker.functions import deliver_webhook_task, flush_audit_logs_task


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
        # The flush helper is reached through the registered worker
        # collaborators (core/worker/registry.py), not a lazy service import,
        # so the seam must be wired and the mock placed where the task looks.
        import src.app.worker  # noqa: F401 - the import is the registration

        with (
            patch(
                "src.app.core.worker.functions.require_feature_collaborators"
            ) as mock_collabs,
            patch(
                "src.app.core.worker.functions.job_bypass_context", return_value=mock_cm
            ),
        ):
            mock_flush = AsyncMock(side_effect=[10, 0])
            mock_collabs.return_value.flush_audit_log_buffer = mock_flush
            mock_collabs.return_value.audit_log_max_flush_per_run = 100
            result = await flush_audit_logs_task({})

            assert "Flushed 10 audit logs" in result

            # Verify flush_audit_log_buffer called with db=mock_session
            # Called twice (once for 10, once for 0)
            assert mock_flush.call_count == 2
            call_kwargs = mock_flush.call_args.kwargs
            assert call_kwargs.get("db") is mock_session


@pytest.mark.asyncio
async def test_deliver_webhook_task_uses_rls_context():
    """Verify deliver_webhook_task passes the RLS session to the delivery seam."""
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
        # `deliver` is reached through the registered collaborators.
        with patch(
            "src.app.core.worker.functions.require_feature_collaborators"
        ) as mock_collabs:
            mock_deliver = AsyncMock()
            mock_collabs.return_value.deliver = mock_deliver
            await deliver_webhook_task(MagicMock(), "d290f1ee-6c54-4b01-90e6-d701748f0851")

            # Verify job_tenant_context called with correct tenant_id
            mock_tenant_ctx_factory.assert_called_once_with(123)

            # Verify the delivery seam got the RLS session and the UUID form
            args = mock_deliver.call_args[0]
            assert args[0] is mock_session
            assert str(args[1]) == "d290f1ee-6c54-4b01-90e6-d701748f0851"
