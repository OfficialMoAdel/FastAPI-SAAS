
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from src.app.core.errors import setup_exception_handlers


@pytest.fixture
def app():
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/db-timeout")
    async def db_timeout_endpoint():
        raise SQLAlchemyTimeoutError("QueuePool limit of size 5 overflow 10 reached, connection timed out", None, None)

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_db_timeout_handler(client):
    """Test that SQLAlchemy TimeoutError returns 503 Service Unavailable."""
    response = client.get("/db-timeout")

    assert response.status_code == 503
    data = response.json()
    assert data["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "Database connection pool exhausted" in data["error"]["message"]
