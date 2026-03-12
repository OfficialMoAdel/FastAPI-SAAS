"""
E2E Validation Script for API Compatibility Endpoints

This script validates all API compatibility endpoints created for frontend parity.
Tests complete workflows with detailed logging of requests and responses.

Run with: python -m pytest tests/e2e/test_api_compat_e2e.py -v -s

REQUIRES: A running server at localhost:8000
"""

import logging
import os
import uuid

import httpx
import pytest

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "http://localhost:8000"
TIMEOUT = 30.0

# Skip all tests in this module if server is not running
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_E2E") != "1",
    reason="Live E2E tests require RUN_LIVE_E2E=1 and a running server at localhost:8000",
)


class TestAPICompatibilityE2E:
    """E2E tests for API compatibility endpoints."""

    @pytest.mark.asyncio
    async def test_account_authentication_workflow(self):
        """Test complete account authentication workflow with /account endpoints."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST: Account Authentication Workflow")
        logger.info("=" * 80)

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            # Step 1: Register new user
            logger.info("\n[Step 1] POST /api/v1/account/register")
            test_id = str(uuid.uuid4())[:8]
            register_data = {
                "name": "API Compat Test User",
                "username": f"compat_test_{test_id}",
                "email": f"compat_test_{test_id}@example.com",
                "password": "SecurePass123!",
            }
            logger.info(f"Request body: {register_data}")

            response = await client.post("/api/v1/account/register", json=register_data)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json() if response.status_code == 201 else response.text}")

            # User may already exist
            if response.status_code not in [201, 400]:
                pytest.fail(f"Registration failed with unexpected status: {response.status_code}")

            # Step 2: Login
            logger.info("\n[Step 2] POST /api/v1/account/login")
            login_data = {"username": register_data["email"], "password": register_data["password"]}
            logger.info(f"Request data: {login_data}")

            response = await client.post("/api/v1/account/login", data=login_data)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")

            assert response.status_code == 200, f"Login failed: {response.text}"
            token_data = response.json()
            assert "access_token" in token_data
            access_token = token_data["access_token"]

            # Step 3: Get current user
            logger.info("\n[Step 3] GET /api/v1/users/me")
            headers = {"Authorization": f"Bearer {access_token}"}
            logger.info(f"Request headers: {headers}")

            response = await client.get("/api/v1/users/me", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")

            assert response.status_code == 200
            user_data = response.json()
            assert user_data["email"] == register_data["email"]

            logger.info("\n✅ Account authentication workflow completed successfully")

    @pytest.mark.asyncio
    async def test_products_and_prices_workflow(self):
        """Test products and prices CRUD workflow."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST: Products and Prices Workflow")
        logger.info("=" * 80)

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            # Get admin token (assuming admin exists)
            logger.info("\n[Step 1] Login as admin")
            response = await client.post("/api/v1/account/login", data={"username": "admin", "password": "admin"})
            if response.status_code != 200:
                pytest.skip("Admin credentials not available for test")

            admin_token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            # Step 2: List products
            logger.info("\n[Step 2] GET /api/v1/products")
            response = await client.get("/api/v1/products", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")
            assert response.status_code == 200

            # Step 3: List prices
            logger.info("\n[Step 3] GET /api/v1/prices")
            response = await client.get("/api/v1/prices", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")
            assert response.status_code == 200

            logger.info("\n✅ Products and prices workflow completed successfully")

    @pytest.mark.asyncio
    async def test_sso_configuration_workflow(self):
        """Test SSO configuration workflow with /ssos endpoints."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST: SSO Configuration Workflow")
        logger.info("=" * 80)

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            # Get admin token
            logger.info("\n[Step 1] Login as admin")
            response = await client.post("/api/v1/account/login", data={"username": "admin", "password": "admin"})
            if response.status_code != 200:
                pytest.skip("Admin credentials not available for test")

            admin_token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            # Step 2: Get available SSO providers
            logger.info("\n[Step 2] GET /api/v1/ssos/available-providers")
            response = await client.get("/api/v1/ssos/available-providers", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")
            assert response.status_code == 200
            providers = response.json()
            assert isinstance(providers, list)
            assert "google" in providers

            # Step 3: List configured SSO settings
            logger.info("\n[Step 3] GET /api/v1/ssos")
            response = await client.get("/api/v1/ssos", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")
            assert response.status_code == 200

            logger.info("\n✅ SSO configuration workflow completed successfully")

    @pytest.mark.asyncio
    async def test_storage_configuration_workflow(self):
        """Test storage configuration workflow."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST: Storage Configuration Workflow")
        logger.info("=" * 80)

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            # Get admin token
            logger.info("\n[Step 1] Login as admin")
            response = await client.post("/api/v1/account/login", data={"username": "admin", "password": "admin"})
            if response.status_code != 200:
                pytest.skip("Admin credentials not available for test")

            admin_token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            # Step 2: Get storage settings
            logger.info("\n[Step 2] GET /api/v1/storage")
            response = await client.get("/api/v1/storage", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json() if response.status_code == 200 else response.text}")
            assert response.status_code in [200, 404]  # 404 if not configured yet

            logger.info("\n✅ Storage configuration workflow completed successfully")

    @pytest.mark.asyncio
    async def test_features_workflow(self):
        """Test feature flags workflow."""
        logger.info("\n" + "=" * 80)
        logger.info("TEST: Feature Flags Workflow")
        logger.info("=" * 80)

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT) as client:
            # Get user token
            logger.info("\n[Step 1] Login as user")
            response = await client.post("/api/v1/account/login", data={"username": "admin", "password": "admin"})
            if response.status_code != 200:
                pytest.skip("User credentials not available for test")

            user_token = response.json()["access_token"]
            headers = {"Authorization": f"Bearer {user_token}"}

            # Step 2: List features
            logger.info("\n[Step 2] GET /api/v1/features")
            response = await client.get("/api/v1/features", headers=headers)
            logger.info(f"Response: {response.status_code}")
            logger.info(f"Response body: {response.json()}")
            assert response.status_code == 200

            logger.info("\n✅ Feature flags workflow completed successfully")


# Add test ID to pytest
def pytest_configure(config):
    """Configure pytest with unique test ID."""
    import time

    pytest.test_id = int(time.time())


if __name__ == "__main__":
    print("""
    E2E API Compatibility Tests
    ===========================

    These tests validate the API compatibility endpoints created for frontend parity.

    Prerequisites:
    - FastAPI server running at http://localhost:8000
    - Admin user configured with credentials: admin/admin
    - Database initialized

    Run with:
        python -m pytest tests/e2e/test_api_compat_e2e.py -v -s
    """)
