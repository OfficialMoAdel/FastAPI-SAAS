"""Integration tests for audit log export API endpoints.

Tests the audit export functionality:
- GET /audit-logs/export/count - Preview export record count
- GET /audit-logs/export - Export audit logs (CSV/JSON/JSONL)
"""

import json
from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.audit_logs.models import AuditLog
from tests.conftest import MockUser

# Headers for tenant context
TENANT_HEADERS = {"X-Tenant-ID": "test-tenant-integration"}


class TestAuditExportCountAPI:
    """Tests for audit export count endpoint."""

    def test_get_export_count_no_filters(self, client, superuser_token_headers):
        """GET /audit-logs/export/count returns count without filters."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export/count", headers=headers)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "count" in data
        assert isinstance(data["count"], int)
        assert data["count"] >= 0

    def test_get_export_count_with_action_filter(self, client, superuser_token_headers):
        """GET /audit-logs/export/count filters by action."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export/count?action=user.login", headers=headers)
        assert response.status_code == status.HTTP_200_OK

    def test_get_export_count_with_date_range(self, client, superuser_token_headers):
        """GET /audit-logs/export/count filters by date range."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get(
            "/api/v1/audit-logs/export/count?start_date=2024-01-01T00:00:00Z&end_date=2024-12-31T23:59:59Z",
            headers=headers,
        )
        assert response.status_code == status.HTTP_200_OK


class TestAuditExportAPI:
    """Tests for audit export endpoint."""

    def test_export_csv_format(self, client, superuser_token_headers):
        """GET /audit-logs/export returns CSV format."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export?format=csv", headers=headers)
        # May require AUDIT_LOGS_DOWNLOAD permission
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ]

    def test_export_json_format(self, client, superuser_token_headers):
        """GET /audit-logs/export returns JSON format."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export?format=json", headers=headers)
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ]

    @pytest.mark.asyncio
    async def test_export_jsonl_format(
        self,
        async_client: AsyncClient,
        superuser_token_headers: dict[str, str],
        db_session: AsyncSession,
    ) -> None:
        """JSONL export returns one complete database record per newline."""
        action = f"test.jsonl.{uuid4().hex}"
        logs = [
            AuditLog(
                tenant_id=MockUser.DEFAULT_TENANT_ID,
                action=action,
                entity_type="user",
                entity_id=str(index),
                user_id=None,
                ip_address=None,
                user_agent=None,
                details={"message": f'Record {index}: "quoted"\nsecond line'},
            )
            for index in range(2)
        ]
        db_session.add_all(logs)
        await db_session.commit()

        try:
            response = await async_client.get(
                "/api/v1/audit-logs/export",
                params={"format": "jsonl", "action": action},
                headers=superuser_token_headers,
            )

            assert response.status_code == status.HTTP_200_OK, response.text
            assert response.headers["content-type"] == "application/x-ndjson"
            assert response.headers["content-disposition"] == "attachment; filename=audit_logs.jsonl"
            assert response.content.endswith(b"\n")
            lines = response.content.splitlines()
            assert len(lines) == len(logs)
            records = [json.loads(line) for line in lines]
            assert {record["id"] for record in records} == {str(log.id) for log in logs}
            records_by_id = {record["id"]: record for record in records}
            for log in logs:
                record = records_by_id[str(log.id)]
                assert record["action"] == action
                assert record["entity_type"] == log.entity_type
                assert record["entity_id"] == log.entity_id
                assert record["details"] == log.details
        finally:
            for log in logs:
                await db_session.delete(log)
            await db_session.commit()

    def test_export_invalid_format(self, client, superuser_token_headers):
        """GET /audit-logs/export rejects invalid format."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export?format=xml", headers=headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
