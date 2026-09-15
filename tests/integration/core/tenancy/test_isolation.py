"""Comprehensive Tenant Isolation Tests.

Consolidates tests from:
- tests/integration/core/tenancy/test_isolation.py
- tests/unit/core/worker/test_rls_context.py
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from uuid6 import uuid7

from src.app.core.worker.rls_context import job_bypass_context, job_tenant_context
from src.app.main import app
from src.app.modules.posts.models import Post
from src.app.modules.tenants.models import Tenant
from src.app.modules.users.models import User
from tests.helpers.auth import clear_test_principal, install_test_principal

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def tenant_alpha(db):
    """Create Tenant Alpha."""
    tenant = db.query(Tenant).filter(Tenant.slug == "alpha").first()
    if not tenant:
        tenant = Tenant(name="Alpha Corp", slug="alpha", is_active=True)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant


@pytest.fixture
def tenant_beta(db):
    """Create Tenant Beta."""
    tenant = db.query(Tenant).filter(Tenant.slug == "beta").first()
    if not tenant:
        tenant = Tenant(name="Beta Inc", slug="beta", is_active=True)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    return tenant


@pytest.fixture
def user_alpha(db, tenant_alpha):
    """Create User in Alpha Corp."""
    user = db.query(User).filter(User.email == "alice@alpha.com", User.tenant_id == tenant_alpha.id).first()
    if not user:
        user = User(
            name="Alice Alpha",
            username="alice_alpha",
            email="alice@alpha.com",
            hashed_password="hashed_secret",
            tenant_id=tenant_alpha.id,
            uuid=uuid7(),
            is_deleted=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@pytest.fixture
def user_beta(db, tenant_beta):
    """Create User in Beta Inc."""
    user = db.query(User).filter(User.email == "bob@beta.com", User.tenant_id == tenant_beta.id).first()
    if not user:
        user = User(
            name="Bob Beta",
            username="bob_beta",
            email="bob@beta.com",
            hashed_password="hashed_secret",
            tenant_id=tenant_beta.id,
            uuid=uuid7(),
            is_deleted=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


@pytest.fixture
def post_alpha(db, user_alpha, tenant_alpha):
    """Create Post for Alice in Alpha."""
    post = Post(
        title="Alpha Secrets",
        text="This is a secret post for Alpha.",
        tenant_id=tenant_alpha.id,
        created_by_user_id=user_alpha.id,
        uuid=uuid7(),
        is_deleted=False,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@pytest.fixture
def post_beta(db, user_beta, tenant_beta):
    """Create Post for Bob in Beta."""
    post = Post(
        title="Beta Trade Secrets",
        text="This is unrelated data for Beta.",
        tenant_id=tenant_beta.id,
        created_by_user_id=user_beta.id,
        uuid=uuid7(),
        is_deleted=False,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


@pytest.mark.skip(reason="Test requires sync/async DB bridge - known architecture limitation")
def test_tenant_isolation_list_posts(client, user_alpha: User, tenant_alpha, post_alpha, post_beta):
    """
    Verify that Alice (Tenant Alpha) can ONLY see Alpha posts.
    """
    install_test_principal(
        app,
        {
            "id": user_alpha.id,
            "username": user_alpha.username,
            "email": user_alpha.email,
            "is_superuser": False,
            "tenant_id": user_alpha.tenant_id,
            "tier_id": None,
        },
        tenant_alpha.id,
    )
    try:
        # R1-08: posts moved under /posts/by-user/{username}
        response = client.get(
            f"/api/v1/posts/by-user/{user_alpha.username}", headers={"X-Tenant-ID": tenant_alpha.slug}
        )

        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()["data"]

        # Debug info
        titles = [p["title"] for p in data]
        print(f"Alice saw posts: {titles}")

        assert len(data) >= 1
        assert any(p["title"] == "Alpha Secrets" for p in data)
        assert not any(p["title"] == "Beta Trade Secrets" for p in data)
    finally:
        clear_test_principal(app)


@pytest.mark.skip(reason="Test requires sync/async DB bridge - known architecture limitation")
def test_cross_tenant_access_denied(client, user_alpha: User, user_beta: User, tenant_alpha, post_beta):
    """
    Verify that Alice (Tenant Alpha) CANNOT access Bob's (Tenant Beta) posts.
    """
    install_test_principal(
        app,
        {
            "id": user_alpha.id,
            "username": user_alpha.username,
            "email": user_alpha.email,
            "is_superuser": False,
            "tenant_id": user_alpha.tenant_id,
            "tier_id": None,
        },
        tenant_alpha.id,
    )
    try:
        response = client.get(
            f"/api/v1/posts/by-user/{user_beta.username}", headers={"X-Tenant-ID": tenant_alpha.slug}
        )

        # Expectation: 403 Forbidden or 404 Not Found
        print(f"Cross-tenant request status: {response.status_code}")
        print(f"Cross-tenant response: {response.text}")

        assert response.status_code in [403, 404]

        if response.status_code == 200:
            data = response.json().get("data", [])
            assert len(data) == 0, "Data Leakage! Alice saw Bob's posts."
    finally:
        clear_test_principal(app)


# =============================================================================
# Unit Tests: RLS Context (Worker)
# =============================================================================


class TestRLSContext:
    """Unit tests for RLS context in background workers."""

    @pytest.mark.asyncio
    async def test_job_tenant_context_yields_session_with_rls(self):
        """Verify job_tenant_context yields session and sets RLS."""
        mock_session = AsyncMock()
        mock_connection = AsyncMock()
        mock_session.connection.return_value = mock_connection

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None

        valid_uuid = "12345678-1234-5678-1234-567812345678"

        with patch("src.app.core.worker.rls_context.local_session", return_value=mock_session_cm):
            async with job_tenant_context(tenant_id=valid_uuid, user_id=456) as db:
                assert db is mock_session

                # Verify RLS was set
                assert mock_connection.execute.called
                calls = mock_connection.execute.call_args_list

                # Check tenant setting
                tenant_set = False
                print(f"DEBUG: Calls: {calls}")
                for call in calls:
                    # call is (args, kwargs) tuple
                    args, kwargs = call
                    sql = args[0] if args else None
                    # Params might be in args[1] or kwargs
                    params = args[1] if len(args) > 1 else kwargs

                    print(f"DEBUG: Checking call - SQL: {sql}, Params: {params}")

                    # SQLAlchemy text object string representation
                    if "app.current_tenant_id" in str(sql) and str(params.get("tenant_id")) == valid_uuid:
                        tenant_set = True
                assert tenant_set, "Tenant ID not set in RLS"

    @pytest.mark.asyncio
    async def test_job_bypass_context_yields_session_with_bypass(self):
        """Verify job_bypass_context yields session and sets bypass."""
        mock_session = AsyncMock()
        mock_connection = AsyncMock()
        mock_session.connection.return_value = mock_connection

        mock_session_cm = MagicMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None

        with patch("src.app.core.worker.rls_context.local_session", return_value=mock_session_cm):
            async with job_bypass_context("Test Reason") as db:
                assert db is mock_session

                # Verify Bypass set
                assert mock_connection.execute.called
                # We don't verify specific SQL here, just that it executed something (RLS bypass)
