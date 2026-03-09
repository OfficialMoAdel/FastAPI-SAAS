from unittest.mock import patch
from uuid import uuid4

import pytest

from src.app.core.db.database import async_get_db
from src.app.core.worker.functions import deliver_webhook_task, sample_background_task
from src.app.main import app

# Import test helpers
from tests.conftest import TestingSessionLocal, override_get_db


@pytest.mark.asyncio
async def test_job_monitoring_e2e_flow(e2e_client, auth_headers):
    """
    E2E Test for Job Monitoring.
    """
    # Override dependency to use test DB
    app.dependency_overrides[async_get_db] = override_get_db

    from contextlib import nullcontext

    with nullcontext():
        # Patch local_session in functions.py so workers use test DB too
        with patch("src.app.core.db.database.local_session", TestingSessionLocal):
            # Now call workers

            # 1. Execute success task
            job_id = f"e2e-job-{uuid4()}"
            ctx = {"job_id": job_id, "job_try": 1, "enqueue_time": 1234567890}

            result = await sample_background_task(ctx, "e2e_test")
            assert result == "Task e2e_test is complete!"

            # 2. Verify metrics via API
            response = await e2e_client.get("/api/v1/admin/jobs/metrics", headers=auth_headers)
            assert response.status_code == 200
            data = response.json()

            assert "metrics" in data
            assert "sample_background_task" in data["metrics"]
            metrics = data["metrics"]["sample_background_task"]
            assert metrics["total"] >= 1
            assert metrics["success"] >= 1

            # 3. Execute failing task
            fail_job_id = f"e2e-fail-{uuid4()}"
            fail_ctx = {"job_id": fail_job_id, "job_try": 1}

            try:
                await deliver_webhook_task(fail_ctx, "invalid-uuid")
            except ValueError:
                pass
            except Exception:
                pass

            # 4. Verify DLQ
            response = await e2e_client.get("/api/v1/admin/jobs/dlq", headers=auth_headers)
            assert response.status_code == 200
            dlq = response.json()

            found = False
            for item in dlq:
                if item["job_id"] == fail_job_id:
                    found = True
                    assert item["function"] == "deliver_webhook_task"
                    break

            assert found

    # Cleanup dependency overrides
    app.dependency_overrides = {}
