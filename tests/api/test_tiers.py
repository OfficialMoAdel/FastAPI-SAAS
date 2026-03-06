import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from src.app.modules.tiers.router import router
from src.app.api.dependencies import get_current_superuser
from src.app.core.db.database import async_get_db

app = FastAPI()
app.include_router(router)

async def override_get_current_superuser():
    return {"is_superuser": True}

async def override_get_current_user_forbidden():
    from src.app.core.exceptions.http_exceptions import ForbiddenException
    raise ForbiddenException("You do not have enough privileges.")

async def override_async_get_db():
    yield AsyncMock()

app.dependency_overrides[async_get_db] = override_async_get_db

client = TestClient(app)

def test_read_tier_unauthenticated():
    response = client.get("/tier/free")
    assert response.status_code == 401

def test_read_tiers_unauthenticated():
    response = client.get("/tiers")
    assert response.status_code == 401

@patch("src.app.modules.tiers.service.tier_service.get_tier", new_callable=AsyncMock)
def test_read_tier_superuser(mock_get_tier):
    app.dependency_overrides[get_current_superuser] = override_get_current_superuser
    mock_get_tier.return_value = {"id": 1, "name": "free", "created_at": "2023-01-01T00:00:00Z"}

    response = client.get("/tier/free")

    # Restore override
    app.dependency_overrides.pop(get_current_superuser)

    assert response.status_code == 200
    assert response.json() == {"id": 1, "name": "free", "created_at": "2023-01-01T00:00:00Z"}
