from unittest.mock import AsyncMock, Mock, patch

import pytest
from uuid import uuid4

# Import test helpers
from tests.conftest import TestingSessionLocal, override_get_db
from src.app.core.db.database import async_get_db
from src.app.main import app
from src.app.core.worker.functions import sample_background_task, deliver_webhook_task


def create_simple_mock_engine():
    """Create a simple mock engine for middleware."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None

    mock_engine = Mock()
    mock_engine.connect.return_value = mock_cm
    return mock_engine


@pytest.mark.asyncio
async def test_job_monitoring_e2e_flow(e2e_client):
    """
    E2E Test for Job Monitoring.
    """
    # Override dependency to use test DB
    app.dependency_overrides[async_get_db] = override_get_db

    # Patch middleware engine to avoid RLS errors
    with patch("src.app.middleware.tenant.async_engine", new=create_simple_mock_engine()):
        # Seeding user:
        from src.app.modules.users.models import User
        from src.app.modules.tenants.models import Tenant
        from src.app.core.security import get_password_hash

        # Use TestingSessionLocal for seeding to write to test DB
        async with TestingSessionLocal() as db:
            # Create tenant first
            tenant = Tenant(name="Test Tenant", slug="test-tenant-e2e")  # distinct slug
            # Check if exists to avoid duplication errors on re-run if DB persists
            # (Though setup_db should clear it session-wide, local_session uses persistent file)
            # Actually, TestingSessionLocal uses memory-shared or file?
            # In conftest: sqlite+aiosqlite:///./test.db
            # So it persists on disk.
            # We should check or try/except.
            from sqlalchemy import select

            result = await db.execute(select(Tenant).where(Tenant.slug == "test-tenant-e2e"))
            existing = result.scalar_one_or_none()

            if not existing:
                db.add(tenant)
                await db.flush()
                tenant_id = tenant.id

                user = User(
                    email="admin@example.com",
                    username="admin",
                    name="Admin User",
                    hashed_password=await get_password_hash("password"),
                    is_superuser=True,
                    is_email_verified=True,
                    tenant_id=tenant_id,
                )
                db.add(user)
                await db.commit()
            else:
                tenant_id = existing.id

        # Patch local_session in functions.py so workers use test DB too
        with patch("src.app.core.db.database.local_session", TestingSessionLocal):
            # Now call workers

            # 1. Execute success task
            job_id = f"e2e-job-{uuid4()}"
            ctx = {"job_id": job_id, "job_try": 1, "enqueue_time": 1234567890}

            result = await sample_background_task(ctx, "e2e_test")
            assert result == "Task e2e_test is complete!"

            # 2. Verify metrics via API
            from src.app.core.security import create_access_token

            token = await create_access_token(data={"sub": "admin@example.com"})
            headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": "test-tenant-e2e"}

            response = await e2e_client.get("/api/v1/admin/jobs/metrics", headers=headers)
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
            response = await e2e_client.get("/api/v1/admin/jobs/dlq", headers=headers)
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

    # Cleanup dependency overrides
    app.dependency_overrides = {}
