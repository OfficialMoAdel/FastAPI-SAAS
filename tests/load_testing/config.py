from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class LoadTestConfig(BaseSettings):
    """Configuration for Locust load tests."""

    # Target Host
    HOST: str = "http://localhost:8000"

    # Test User Credentials (Admin/Superuser for setup)
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "Str1ngst!"

    # Default Test Tenant
    TEST_TENANT_ID: str | None = "1"  # Can be overridden or fetched dynamically

    # Load Test Parameters
    WAIT_TIME_MIN: float = 1.0
    WAIT_TIME_MAX: float = 3.0

    # File Upload Testing
    UPLOAD_FILE_SIZE_KB: int = 100

    model_config = SettingsConfigDict(
        env_prefix="LOAD_TEST_",
        env_file=".env.loadtest",
        extra="ignore"
    )

config = LoadTestConfig()
