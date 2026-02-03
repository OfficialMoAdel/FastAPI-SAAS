import logging
import time
from locust import HttpUser, task, between, events
from locust.exception import StopUser

from tests.load_testing.config import config
from tests.load_testing.utils import generate_dummy_file

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SaaSUser(HttpUser):
    wait_time = between(config.WAIT_TIME_MIN, config.WAIT_TIME_MAX)

    def on_start(self):
        """
        Executed when a simulated user starts.
        Performs login to obtain access token.
        """
        self.email = config.ADMIN_EMAIL
        self.password = config.ADMIN_PASSWORD
        self.access_token = None
        self.refresh_token = None
        self.tenant_id = config.TEST_TENANT_ID

        # Authenticate
        self.login()

        # If tenant ID isn't provided, try to fetch the first available tenant
        if not self.tenant_id:
            self.fetch_tenant()

    def login(self):
        """Authenticate and store tokens."""
        try:
            response = self.client.post(
                "/api/v1/account/login",
                data={"username": self.email, "password": self.password},
                name="/api/v1/account/login"
            )

            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                # Refresh token is typically in cookies or response body depending on implementation
                # Based on router code read earlier, refresh logic might look for cookie
                self.refresh_token = response.cookies.get("refresh_token")

                # Set default headers for subsequent requests
                self.client.headers.update({
                    "Authorization": f"Bearer {self.access_token}"
                })
                logger.info(f"User {self.email} logged in successfully")
            else:
                logger.error(f"Login failed for {self.email}: {response.text}")
                self.stop(force=True)

        except Exception as e:
            logger.error(f"Login exception: {e}")
            self.stop(force=True)

    def fetch_tenant(self):
        """Fetch a tenant ID if not configured."""
        if not self.access_token:
            return

        response = self.client.get("/api/v1/tenants", name="/api/v1/tenants")
        if response.status_code == 200:
            tenants = response.json()
            if isinstance(tenants, list) and len(tenants) > 0:
                self.tenant_id = tenants[0]["id"]
            elif isinstance(tenants, dict) and "items" in tenants and len(tenants["items"]) > 0:
                 self.tenant_id = tenants["items"][0]["id"]

            if self.tenant_id:
                # Add tenant header if required by middleware
                # self.client.headers.update({"X-Tenant-ID": str(self.tenant_id)})
                logger.info(f"Using Tenant ID: {self.tenant_id}")
            else:
                logger.warning("No tenants found available for test user")

    @task(3)
    def get_user_profile(self):
        """Load profile information (common read operation)."""
        if not self.access_token:
            return
        self.client.get("/api/v1/users/me", name="/api/v1/users/me")

    @task(1)
    def list_users(self):
        """List users (admin operation)."""
        if not self.access_token:
            return
        self.client.get("/api/v1/users", name="/api/v1/users")

    @task(2)
    def refresh_token_check(self):
        """Test token refresh flow."""
        # Note: This might invalidate current access token in some implementations
        # So we just verify the endpoint responds, but don't necessarily replace our session token
        # unless we want to simulate long-lived sessions.
        if not self.refresh_token:
            return

        self.client.post(
            "/api/v1/account/refresh",
            name="/api/v1/account/refresh",
            # Cookies are handled automatically by Session
        )

    @task(1)
    def get_tenant_info(self):
        """Get tenant details."""
        if not self.tenant_id:
            return
        self.client.get(f"/api/v1/tenants/{self.tenant_id}", name="/api/v1/tenants/{id}")

    @task(1)
    def file_operations(self):
        """Upload and then download a file."""
        if not self.access_token:
            return

        # 1. Upload
        filename, file_io, mime_type = generate_dummy_file(config.UPLOAD_FILE_SIZE_KB)
        files = {"file": (filename, file_io, mime_type)}

        # Depending on API, we might need tenant_id in query or path
        # Assuming standard /files/upload
        response = self.client.post(
            "/api/v1/files/upload",
            files=files,
            name="/api/v1/files/upload"
        )

        if response.status_code == 200 or response.status_code == 201:
            file_data = response.json()
            file_id = file_data.get("id")

            # 2. Download/Get info
            if file_id:
                self.client.get(f"/api/v1/files/{file_id}", name="/api/v1/files/{id}")

    @task(1)
    def view_audit_logs(self):
        """Check audit logs (read operation)."""
        if not self.access_token:
            return
        self.client.get("/api/v1/audit-logs", name="/api/v1/audit-logs")

    def on_stop(self):
        """Cleanup actions."""
        # Optional: Logout
        # self.client.post("/api/v1/account/logout", name="/api/v1/account/logout")
        pass
