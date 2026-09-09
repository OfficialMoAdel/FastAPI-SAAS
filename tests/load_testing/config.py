from pydantic_settings import BaseSettings, SettingsConfigDict


class LoadTestConfig(BaseSettings):
    """Configuration for Locust load tests."""

    # Target Host
    HOST: str = "http://localhost:8000"

    # Test User Credentials (Admin/Superuser for setup)
    ADMIN_EMAIL: str = "premium_e2e@example.com"
    # C-1 hygiene: not a git-history-leaked value. Override via LOAD_TEST_ADMIN_PASSWORD.
    ADMIN_PASSWORD: str = "LoadTestAdminPw?a1B2c3D4e5f6"

    # Default Test Tenant
    TEST_TENANT_ID: str | None = "test-tenant-e2e"  # Can be overridden or fetched dynamically

    # Load Test Parameters
    WAIT_TIME_MIN: float = 1.0
    WAIT_TIME_MAX: float = 3.0

    # Profiles: smoke, load, stress
    PROFILE: str = "smoke"

    # Stress Profile Defaults (override via env vars)
    STRESS_USERS: int = 1000
    STRESS_SPAWN_RATE: int = 50

    # File Upload Testing
    UPLOAD_FILE_SIZE_KB: int = 100

    model_config = SettingsConfigDict(env_prefix="LOAD_TEST_", env_file=".env.loadtest", extra="ignore")


config = LoadTestConfig()
