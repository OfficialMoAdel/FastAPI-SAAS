import bcrypt
import pytest

from src.app.core.utils.cache import async_get_redis
from src.app.main import app
from src.app.modules.users.models import User


def get_password_hash_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        val = self.store.get(key, 0)
        val = int(val) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key, seconds):
        return True

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
        return True

    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True

    async def aclose(self):
        pass


fake_redis_instance = FakeRedis()


async def get_fake_redis():
    yield fake_redis_instance


@pytest.fixture
def locked_out_user(db):
    user = User(
        name="Lockout Test",
        username="lockoutuser",
        email="lockout@example.com",
        element_id="lockout",  # Assuming this field if needed, or dummy
        hashed_password=get_password_hash_sync("password123"),
        profile_image_url="http://example.com",
        tenant_id=1,  # Assuming tenant 1 exists from seeded data or other tests
        uuid="cb5c0b12-9c98-4c12-9c98-4c129c984c12",
    )
    # Check if exists
    existing = db.query(User).filter_by(username="lockoutuser").first()
    if existing:
        return existing

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_account_lockout(client_auth, db, tenant):  # tenant fixture ensures a tenant
    # Ensure user exists for authentication attempt
    hashed_pw = get_password_hash_sync("password123")
    user = db.query(User).filter_by(username="lockoutuser").first()
    if not user:
        user = User(
            name="Lockout Test",
            username="lockoutuser",
            email="lockout@example.com",
            hashed_password=hashed_pw,
            profile_image_url="http://example.com",
            tenant_id=tenant.id,
            is_superuser=False,
            is_deleted=False,
        )
        db.add(user)
        db.commit()

    # Override redis
    fake_redis_instance.store.clear()
    app.dependency_overrides[async_get_redis] = lambda: fake_redis_instance

    try:
        # 1. 5 failed attempts
        for i in range(5):
            response = client_auth.post(
                "/api/v1/account/login",
                data={"username": "lockoutuser", "password": "wrongpassword"},
                headers={"X-Tenant-ID": tenant.slug},
            )
            assert response.status_code == 401
            assert "Wrong username" in response.json()["detail"]

        # 2. 6th attempt -> Locked
        response = client_auth.post(
            "/api/v1/account/login",
            data={"username": "lockoutuser", "password": "wrongpassword"},
            headers={"X-Tenant-ID": tenant.slug},
        )
        assert response.status_code == 401
        assert "Account locked" in response.json()["detail"]

        # 3. Even with CORRECT password -> Locked
        response = client_auth.post(
            "/api/v1/account/login",
            data={"username": "lockoutuser", "password": "password123"},
            headers={"X-Tenant-ID": tenant.slug},
        )
        assert response.status_code == 401
        assert "Account locked" in response.json()["detail"]

        # 4. Clear lockout (simulate expire)
        fake_redis_instance.store.clear()

        # 5. Correct password -> Success
        response = client_auth.post(
            "/api/v1/account/login",
            data={"username": "lockoutuser", "password": "password123"},
            headers={"X-Tenant-ID": tenant.slug},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    finally:
        del app.dependency_overrides[async_get_redis]
