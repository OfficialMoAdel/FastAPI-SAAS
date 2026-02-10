import logging
from io import BytesIO
from typing import Final

from locust import HttpUser, between, task

from tests.load_testing.config import config
from tests.load_testing.utils import generate_dummy_file

# Configure logging
logging.basicConfig(level=logging.INFO)
logger: Final = logging.getLogger(__name__)

# Cache for dummy file to avoid regeneration cpu cost
_DUMMY_FILE_CACHE: Final[dict[int, tuple]] = {}

# Retry configuration
LOGIN_RETRY_COUNT: Final[int] = 3
LOGIN_RETRY_DELAY_S: Final[float] = 1.0

# Task weights
WEIGHT_USER_PROFILE: Final[int] = 3
WEIGHT_LIST_USERS: Final[int] = 1
WEIGHT_REFRESH_TOKEN: Final[int] = 2
WEIGHT_TENANT_INFO: Final[int] = 1
WEIGHT_FILE_OPS: Final[int] = 1
WEIGHT_AUDIT_LOGS: Final[int] = 1


def get_cached_dummy_file(size_kb: int) -> tuple[str, BytesIO, str]:
    """Get cached dummy file or generate if not exists."""
    if size_kb not in _DUMMY_FILE_CACHE:
        _DUMMY_FILE_CACHE[size_kb] = generate_dummy_file(size_kb)

    filename, content, mime = _DUMMY_FILE_CACHE[size_kb]
    # Return a fresh IO object but reuse content
    return filename, BytesIO(content.getvalue()), mime

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

        # Set tenant header if configured
        if self.tenant_id:
            # logger.info(f"Using configured Tenant ID: {self.tenant_id}")
            self.client.headers.update({"X-Tenant-ID": str(self.tenant_id)})

        # Authenticate with retries
        for i in range(LOGIN_RETRY_COUNT):
            if self.login():
                break
            import time
            time.sleep(LOGIN_RETRY_DELAY_S)

        # If tenant ID isn't provided, try to fetch the first available tenant
        if not self.tenant_id:
            self.fetch_tenant()

    def login(self):
        """Authenticate and store tokens."""
        try:
            # Check if tenant header is set, if not, we can't login effectively unless exempt
            if "X-Tenant-ID" not in self.client.headers:
                logger.warning("Attempting login without X-Tenant-ID header")

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
                # logger.info(f"User {self.email} logged in successfully")
                return True
            else:
                logger.error(f"Login failed for {self.email}: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Login exception: {e}")
            return False

    def fetch_tenant(self):
        """Fetch a tenant ID if not configured."""
        if not self.access_token:
            return

        # We might not be able to fetch tenants if we are not logged in or lack tenant context
        # But if we managed to login (e.g. if login was exempt or we guessed tenant), we can try.
        # Note: In this system, login requires tenant header. So this method is only useful
        # if we somehow logged in without tenant (impossible) OR if we want to switch tenants.
        # Or maybe there's a default/public tenant.

        # In this specific issue, we failed login because of missing tenant.
        # So fetch_tenant is never reached if login fails.
        pass

    @task(WEIGHT_USER_PROFILE)
    def get_user_profile(self):
        """Load profile information (common read operation)."""
        if not self.access_token:
            return
        self.client.get("/api/v1/users/me", name="/api/v1/users/me")

    @task(WEIGHT_LIST_USERS)
    def list_users(self):
        """List users (admin operation)."""
        if not self.access_token:
            return
        self.client.get("/api/v1/users", name="/api/v1/users")

    @task(WEIGHT_REFRESH_TOKEN)
    def refresh_token_check(self):
        """Test token refresh flow."""
        # Note: This might invalidate current access token in some implementations
        # So we just verify the endpoint responds, but don't necessarily replace our session token
        # unless we want to simulate long-lived sessions.
        if not self.refresh_token:
            return

        # Explicitly set cookie for refresh if needed by framework,
        # though Requests/Locust usually handles cookies automatically
        cookies = None
        if self.refresh_token:
             cookies = {"refresh_token": self.refresh_token}

        self.client.post(
            "/api/v1/account/refresh",
            name="/api/v1/account/refresh",
            cookies=cookies
        )

    @task(WEIGHT_TENANT_INFO)
    def get_tenant_info(self):
        """Get tenant details."""
        if not self.tenant_id:
            return
        self.client.get("/api/v1/tenants/1", name="/api/v1/tenants/{id}") # Use ID 1 assuming it matches test tenant

    @task(WEIGHT_FILE_OPS)
    def file_operations(self):
        """Upload and then download a file."""
        if not self.access_token:
            return

        # 1. Upload - use cached file generation
        filename, file_io, mime_type = get_cached_dummy_file(config.UPLOAD_FILE_SIZE_KB)
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

    @task(WEIGHT_AUDIT_LOGS)
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
