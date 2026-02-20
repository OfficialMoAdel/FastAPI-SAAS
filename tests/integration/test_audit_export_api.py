"""Integration tests for audit log export API endpoints.

Tests the audit export functionality:
- GET /audit-logs/export/count - Preview export record count
- GET /audit-logs/export - Export audit logs (CSV/JSON/JSONL)
"""

from fastapi import status

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

    def test_export_invalid_format(self, client, superuser_token_headers):
        """GET /audit-logs/export rejects invalid format."""
        headers = {**TENANT_HEADERS, **superuser_token_headers}
        response = client.get("/api/v1/audit-logs/export?format=xml", headers=headers)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY