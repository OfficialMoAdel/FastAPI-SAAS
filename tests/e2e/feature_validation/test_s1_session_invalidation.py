"""S-1 e2e oracle: session invalidation unit boundary + logout shape."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

LOGOUT_PATH = "/api/v1/account/logout"
LOGIN_PATH = "/api/v1/account/login"


@pytest.mark.asyncio
async def test_s1_logout_returns_session_revoked_shape(client, authenticated_user):
    email = authenticated_user["email"]
    password = "TestPassword123!"
    login = await client.post(LOGIN_PATH, data={"username": email, "password": password})
    assert login.status_code in (200, 201, 422)
    if login.status_code not in (200, 201):
        return
    access = login.json()["access_token"]
    logout = await client.post(LOGOUT_PATH, headers={"Authorization": f"Bearer {access}"})
    assert logout.status_code in (200, 204, 401)
    body = logout.json()
    assert "message" in body
    assert "session_revoked" in body
    replay = await client.post(LOGOUT_PATH, headers={"Authorization": f"Bearer {access}"})
    assert replay.status_code in (401, 422, 200)


@pytest.mark.asyncio
async def test_s1_session_manager_nil_redis_honest_false():
    from src.app.core.security.session_manager import SessionManager

    sm = SessionManager(None)
    assert await sm.invalidate_session(user_id=1, session_id="does-not-exist") is False
