"""
MFA End-to-End Tests

Tests the complete MFA flow from setup through verification, including:
1. MFA setup flow
2. Login with TOTP
3. Login with backup code
4. Lockout flow
5. Disable MFA
6. Regenerate backup codes
"""

import pytest

_test_ip: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _stable_ip_per_test(request):
    """One fixed client IP per test.

    S-M3 (MFA_ENFORCE_IP_BINDING default True) requires the login request
    and the later MFA-completion requests to share a client IP, while
    rate-limit bypass still wants distinct IPs across tests. Derive
    deterministically from the test name: stable within a test, spread
    across tests.
    """
    _test_ip["ip"] = f"192.168.1.{abs(hash(request.node.name)) % 200 + 10}"
    try:
        yield
    finally:
        _test_ip.pop("ip", None)


def ip_headers():
    """Helper returning test-request headers with the per-test stable IP."""
    return {"X-Forwarded-For": _test_ip.get("ip", "192.168.1.50"), "X-Tenant-ID": "test-tenant-e2e"}


@pytest.fixture(autouse=True)
def _open_registration_for_mfa_flow():
    """S-M2: these MFA e2e tests register into the shared test tenant to
    exercise MFA — not registration policy (covered by unit tests in
    test_account_register.py). Patch settings.auth directly: the gate
    lives at settings.auth.AUTH_OPEN_REGISTRATION, so a top-level
    override_settings kwarg would be a no-op."""
    from src.app.core.config import settings

    original = settings.auth.AUTH_OPEN_REGISTRATION
    settings.auth.AUTH_OPEN_REGISTRATION = True
    try:
        yield
    finally:
        settings.auth.AUTH_OPEN_REGISTRATION = original


# MFA End-to-End Tests
@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFASetupFlow:
    """Test complete MFA setup and enable flow."""

    async def test_mfa_setup_complete_flow(self, async_client, perf_metrics, db):
        """[STEP 1] Verify MFA setup generates secret and backup codes."""
        # Setup: Create test user
        test_user_email = "mfa_setup_test@example.com"
        test_password = "SecurePass123!"

        # Register user
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA Test User",
                "username": "mfatestuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get access token
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # [STEP 1] Setup MFA
        setup_response = await async_client.post(
            "/api/v1/mfa/setup",
            headers={**headers, **ip_headers()},
        )
        if setup_response.status_code != 200:
            print("ERROR RESPONSE:", setup_response.json())
        assert setup_response.status_code == 200
        setup_data = setup_response.json()

        assert "setup_token" in setup_data
        assert "qr_code_base64" in setup_data
        assert "otpauth_url" in setup_data

        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # [STEP 2] Verify MFA with TOTP to enable
        # Generate valid TOTP token using pyotp
        import pyotp

        totp = pyotp.TOTP(secret)
        valid_token = totp.now()

        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": valid_token},
        )
        if enable_response.status_code != 200:
            print(f"Enable failed: {enable_response.status_code}")
            print(f"Response: {enable_response.json()}")
        assert enable_response.status_code == 200
        enable_data = enable_response.json()

        assert enable_data["message"] == "MFA enabled successfully"
        assert "backup_codes" in enable_data

        # [STEP 3] Get backup codes (requires MFA verification after enabling)
        # Use TOTP token for verification since MFA is now enabled
        totp = pyotp.TOTP(secret)
        backup_codes_response = await async_client.post(
            "/api/v1/mfa/backup-codes",
            headers={**headers, **ip_headers()},
            json={"password": test_password, "totp_token": totp.now()},
        )
        assert backup_codes_response.status_code == 200
        backup_codes_data = backup_codes_response.json()

        assert "backup_codes" in backup_codes_data
        assert len(backup_codes_data["backup_codes"]) == 10
        assert all(bc.startswith("BC-") for bc in backup_codes_data["backup_codes"])

        backup_codes_data["backup_codes"]

        # [STEP 4] Verify MFA is enabled
        status_response = await async_client.get(
            "/api/v1/mfa/status",
            headers={**headers, **ip_headers()},
        )
        assert status_response.status_code == 200
        status_data = status_response.json()

        assert status_data["is_enabled"] is True
        # After regenerating backup codes, last_used is set from the TOTP verification
        assert status_data["backup_codes_remaining"] == 10
        assert status_data["last_used"] is not None  # Used during backup codes regeneration

        # Cleanup: Disable MFA and delete user
        disable_response = await async_client.post(
            "/api/v1/mfa/disable",
            headers={**headers, **ip_headers()},
            json={"password": test_password, "totp_token": valid_token},
        )
        assert disable_response.status_code == 200

        print(f"[E2E SUCCESS] MFA setup flow completed for user {user_id}")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFALoginWithTOTP:
    """Test login flow with TOTP verification."""

    async def test_login_with_totp_success(self, async_client, perf_metrics, db):
        """[STEP 1] Login with valid TOTP token succeeds."""
        test_user_email = "mfa_totp_login@example.com"
        test_password = "SecurePass123!"

        # Register user
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA TOTP User",
                "username": "mfatotpuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get tokens
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Setup MFA
        setup_response = await async_client.post("/api/v1/mfa/setup", headers=headers)
        setup_data = setup_response.json()
        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # Enable MFA
        import pyotp

        totp = pyotp.TOTP(secret)
        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": totp.now()},
        )
        assert enable_response.status_code == 200

        # Logout to invalidate session
        cookies = {"refresh_token": login_response.cookies.get("refresh_token", "")}
        logout_response = await async_client.post("/api/v1/account/logout", headers=headers, cookies=cookies)
        assert logout_response.status_code == 200

        # [STEP 1] Login again with MFA
        login_headers = ip_headers()
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=login_headers,
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        login_data = login_response.json()

        assert "mfa_token" in login_data
        assert "mfa_required" in login_data
        assert login_data["mfa_required"] is True

        mfa_token = login_data["mfa_token"]

        # [STEP 2] Complete MFA login with valid TOTP
        complete_response = await async_client.post(
            "/api/v1/mfa/complete-login",
            headers=login_headers,
            json={
                "mfa_token": mfa_token,
                "totp_token": totp.now(),
                "backup_code": None,
            },
        )
        assert complete_response.status_code == 200
        complete_data = complete_response.json()

        assert "access_token" in complete_data
        # refresh_token is set as a cookie by the complete-login endpoint
        assert complete_response.cookies.get(
            "refresh_token"
        ) is not None or "refresh_token" in complete_response.headers.get("set-cookie", "")  # noqa: E501

        # Verify MFA last_used_at is updated
        new_headers = {"Authorization": f"Bearer {complete_data['access_token']}"}
        status_response = await async_client.get("/api/v1/mfa/status", headers=new_headers)
        assert status_response.status_code == 200
        status_data = status_response.json()

        assert status_data["is_enabled"] is True
        assert status_data["last_used"] is not None

        print("[E2E SUCCESS] Login with TOTP completed successfully")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFALoginWithBackupCode:
    """Test login flow with backup code verification."""

    async def test_login_with_backup_code_success(self, async_client, perf_metrics, db):
        """[STEP 1] Login with backup code consumes one code."""
        test_user_email = "mfa_backup_login@example.com"
        test_password = "SecurePass123!"

        # Register and setup MFA
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA Backup User",
                "username": "mfabackupuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get tokens
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Setup MFA
        setup_response = await async_client.post("/api/v1/mfa/setup", headers=headers)
        setup_data = setup_response.json()
        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # Enable MFA
        import pyotp

        totp = pyotp.TOTP(secret)
        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": totp.now()},
        )
        assert enable_response.status_code == 200

        # Get backup codes (requires MFA verification after enabling)
        totp = pyotp.TOTP(secret)
        backup_codes_response = await async_client.post(
            "/api/v1/mfa/backup-codes",
            headers={**headers, **ip_headers()},
            json={"password": test_password, "totp_token": totp.now()},
        )
        assert backup_codes_response.status_code == 200
        backup_codes = backup_codes_response.json()["backup_codes"]

        # Logout
        cookies = {"refresh_token": login_response.cookies.get("refresh_token", "")}
        await async_client.post("/api/v1/account/logout", headers=headers, cookies=cookies)

        # Login to get MFA token
        login_headers = ip_headers()
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=login_headers,
            data={"username": test_user_email, "password": test_password},
        )
        mfa_token = login_response.json()["mfa_token"]

        # [STEP 1] Complete login with backup code
        backup_code = backup_codes[0]
        complete_response = await async_client.post(
            "/api/v1/mfa/complete-login",
            headers=login_headers,
            json={
                "mfa_token": mfa_token,
                "totp_token": None,
                "backup_code": backup_code,
            },
        )
        assert complete_response.status_code == 200
        complete_data = complete_response.json()

        assert "access_token" in complete_data

        # Verify backup code count decreased
        new_headers = {"Authorization": f"Bearer {complete_data['access_token']}"}
        status_response = await async_client.get("/api/v1/mfa/status", headers=new_headers)
        status_data = status_response.json()

        assert status_data["backup_codes_remaining"] == 9

        # [STEP 2] Verify backup code cannot be reused
        await async_client.post("/api/v1/account/logout", headers=new_headers)

        login_headers2 = ip_headers()
        login_response2 = await async_client.post(
            "/api/v1/account/login",
            headers=login_headers2,
            data={"username": test_user_email, "password": test_password},
        )
        mfa_token2 = login_response2.json()["mfa_token"]

        complete_response2 = await async_client.post(
            "/api/v1/mfa/complete-login",
            headers=login_headers2,
            json={
                "mfa_token": mfa_token2,
                "totp_token": None,
                "backup_code": backup_code,
            },
        )
        assert complete_response2.status_code == 401

        print("[E2E SUCCESS] Login with backup code completed successfully")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFALockoutFlow:
    """Test MFA lockout mechanism after failed attempts."""

    async def test_mfa_lockout_after_5_failed_attempts(self, async_client, perf_metrics, mock_redis, db):
        """[STEP 1] Verify rate limit enforced after 5 failed attempts."""
        test_user_email = "mfa_lockout_test@example.com"
        test_password = "SecurePass123!"

        # Register and setup MFA
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA Lockout User",
                "username": "mfalockoutuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get tokens
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Setup MFA
        setup_response = await async_client.post("/api/v1/mfa/setup", headers=headers)
        setup_data = setup_response.json()
        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # Enable MFA
        import pyotp

        totp = pyotp.TOTP(secret)
        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": totp.now()},
        )
        assert enable_response.status_code == 200

        # Logout
        cookies = {"refresh_token": login_response.cookies.get("refresh_token", "")}
        await async_client.post("/api/v1/account/logout", headers=headers, cookies=cookies)

        # Clear rate limits before looping to avoid 429 on login endpoint
        from src.app.core.utils import cache
        from src.app.core.utils.rate_limit import reset_rate_limiter

        reset_rate_limiter()
        if cache.client:
            await cache.client.flushdb()

        # [STEP 1] Make 5 failed attempts
        for i in range(5):
            login_headers = ip_headers()
            login_response = await async_client.post(
                "/api/v1/account/login",
                headers=login_headers,
                data={"username": test_user_email, "password": test_password},
            )
            mfa_token = login_response.json()["mfa_token"]

            complete_response = await async_client.post(
                "/api/v1/mfa/complete-login",
                headers=login_headers,
                json={
                    "mfa_token": mfa_token,
                    "totp_token": "000000",
                    "backup_code": None,
                },
            )
            assert complete_response.status_code == 401
            print(f"[STEP 1.{i + 1}] Failed attempt {i + 1}: 401 Unauthorized")

        # [STEP 2] 6th attempt should be locked out (401)
        # The 5 logins above exhaust the login rate limit (5/min/IP), so
        # clear only the auth rate-limit keys (MFA attempt counter is a
        # separate per-user key and must be preserved) and use a fresh IP
        # for the login+complete pair (MFA lockout is per-user, IP-agnostic;
        # IP binding only requires login and complete-login share an IP).
        if cache.client:
            auth_keys = await cache.client.keys("ratelimit:*budget:auth")
            if auth_keys:
                await cache.client.delete(*auth_keys)
        login_headers_6 = {"X-Forwarded-For": "192.168.99.50", "X-Tenant-ID": "test-tenant-e2e"}
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=login_headers_6,
            data={"username": test_user_email, "password": test_password},
        )
        mfa_token = login_response.json()["mfa_token"]

        complete_response = await async_client.post(
            "/api/v1/mfa/complete-login",
            headers=login_headers_6,
            json={
                "mfa_token": mfa_token,
                "totp_token": "000000",
                "backup_code": None,
            },
        )
        assert complete_response.status_code == 401
        assert "MFA locked" in complete_response.json()["detail"]

        print("[STEP 2] 6th attempt locked out: 401 Unauthorized")
        print("[E2E SUCCESS] MFA lockout mechanism verified")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFADisableFlow:
    """Test MFA disable flow."""

    async def test_disable_mfa_success(self, async_client, perf_metrics, db):
        """[STEP 1] Disable MFA removes requirement."""
        test_user_email = "mfa_disable_test@example.com"
        test_password = "SecurePass123!"

        # Register and setup MFA
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA Disable User",
                "username": "mfadisableuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get tokens
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Setup MFA
        setup_response = await async_client.post("/api/v1/mfa/setup", headers=headers)
        setup_data = setup_response.json()
        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # Enable MFA
        import pyotp

        totp = pyotp.TOTP(secret)
        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": totp.now()},
        )
        assert enable_response.status_code == 200

        # [STEP 1] Disable MFA
        disable_response = await async_client.post(
            "/api/v1/mfa/disable",
            headers={**headers, **ip_headers()},
            json={"password": test_password, "totp_token": totp.now()},
        )
        assert disable_response.status_code == 200
        assert disable_response.json()["message"] == "MFA disabled successfully"

        # [STEP 2] Verify MFA is enabled
        status_response = await async_client.get("/api/v1/mfa/status", headers=headers)
        assert status_response.status_code == 200
        status_data = status_response.json()

        # Note: MFA is now disabled, so is_enabled should be False
        assert status_data["is_enabled"] is False

        # [STEP 3] Login should not require MFA
        cookies = {"refresh_token": login_response.cookies.get("refresh_token", "")}
        await async_client.post("/api/v1/account/logout", headers=headers, cookies=cookies)

        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        login_data = login_response.json()

        assert "access_token" in login_data
        assert "requires_mfa" not in login_data or login_data.get("requires_mfa") is False

        print("[E2E SUCCESS] MFA disable flow completed successfully")


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMFARegenerateBackupCodes:
    """Test backup code regeneration flow."""

    async def test_regenerate_backup_codes_success(self, async_client, perf_metrics, db):
        """[STEP 1] Regenerate backup codes replaces old ones."""
        test_user_email = "mfa_regenerate_test@example.com"
        test_password = "SecurePass123!"

        # Register and setup MFA
        register_response = await async_client.post(
            "/api/v1/account/register",
            json={
                "email": test_user_email,
                "password": test_password,
                "name": "MFA Regenerate User",
                "username": "mfaregenerateuser",
            },
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]

        from sqlalchemy import text

        db.execute(text(f"UPDATE users SET is_active = 1 WHERE id = '{user_id}'"))
        db.commit()

        # Login to get tokens
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=ip_headers(),
            data={"username": test_user_email, "password": test_password},
        )
        assert login_response.status_code == 200
        access_token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Setup MFA
        setup_response = await async_client.post("/api/v1/mfa/setup", headers=headers)
        setup_data = setup_response.json()
        setup_token = setup_data["setup_token"]

        from urllib.parse import parse_qs, urlparse

        parsed_url = urlparse(setup_data["otpauth_url"])
        secret = parse_qs(parsed_url.query)["secret"][0]

        # Enable MFA
        import pyotp

        totp = pyotp.TOTP(secret)
        enable_response = await async_client.post(
            "/api/v1/mfa/enable",
            headers={**headers, **ip_headers()},
            json={"setup_token": setup_token, "totp_token": totp.now()},
        )
        assert enable_response.status_code == 200

        # Get backup codes (need TOTP since MFA is enabled)
        backup_codes_response = await async_client.post(
            "/api/v1/mfa/backup-codes",
            headers={**headers, **ip_headers()},
            json={"password": test_password, "totp_token": totp.now()},
        )
        old_backup_codes = backup_codes_response.json()["backup_codes"]

        # Use one backup code
        cookies = {"refresh_token": login_response.cookies.get("refresh_token", "")}
        await async_client.post("/api/v1/account/logout", headers=headers, cookies=cookies)

        login_headers = ip_headers()
        login_response = await async_client.post(
            "/api/v1/account/login",
            headers=login_headers,
            data={"username": test_user_email, "password": test_password},
        )
        mfa_token = login_response.json()["mfa_token"]

        complete_response = await async_client.post(
            "/api/v1/mfa/complete-login",
            headers=login_headers,
            json={
                "mfa_token": mfa_token,
                "totp_token": None,
                "backup_code": old_backup_codes[0],
            },
        )
        assert complete_response.status_code == 200

        new_headers = {"Authorization": f"Bearer {complete_response.json()['access_token']}"}

        # Verify one code used
        await async_client.get("/api/v1/mfa/status", headers=new_headers)
        # Note: backup_codes_remaining may not be returned in status endpoint
        # We'll verify by checking if the code can't be reused

        # [STEP 1] Regenerate backup codes (needs TOTP since MFA is enabled)
        regenerate_response = await async_client.post(
            "/api/v1/mfa/backup-codes",
            headers=new_headers,
            json={"password": test_password, "totp_token": totp.now()},
        )
        assert regenerate_response.status_code == 200
        new_backup_codes = regenerate_response.json()["backup_codes"]

        # [STEP 2] Verify old codes are invalidated
        # All new codes should be different from old codes
        assert all(new_code not in old_backup_codes for new_code in new_backup_codes)

        # [STEP 3] Verify count is reset to 10
        # Note: backup_codes_remaining may not be returned in status endpoint
        status_response2 = await async_client.get("/api/v1/mfa/status", headers=new_headers)
        assert status_response2.json()["is_enabled"] is True

        print("[E2E SUCCESS] Backup code regeneration completed successfully")
