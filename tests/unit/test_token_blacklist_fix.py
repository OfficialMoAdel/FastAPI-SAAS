from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Mock settings for testing
from src.app.core.db.crud_token_blacklist import crud_token_blacklist
from src.app.core.db.database import Base
from src.app.core.schemas import TokenBlacklistCreate


@pytest.mark.asyncio
async def test_token_blacklist_timezone_handling():
    """Test that token blacklisting handles timezones correctly without crashing."""

    # Setup in-memory SQLite for testing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # Create a dummy token string
        token = "dummy_token_string"

        # Create an expires_at with timezone (UTC)
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        # Try to create a blacklist entry directly via CRUD
        # This simulates what blacklist_tokens does at the DB level
        try:
            # Use proper Pydantic model for create
            token_create = TokenBlacklistCreate(token=token, expires_at=expires_at)
            await crud_token_blacklist.create(db, object=token_create)
            print("Successfully created blacklist entry with aware datetime")
        except Exception as e:
            pytest.fail(f"Failed to create blacklist entry with aware datetime: {e}")

        # Verify we can read it back and it has timezone info (or at least consistent)
        stored = await crud_token_blacklist.get(db, token=token)
        assert stored is not None
        assert stored["token"] == token
        # SQLite might lose timezone info depending on driver, but the insertion shouldn't fail

        # Test the comparison logic in get_active_tokens
        try:
            active = await crud_token_blacklist.get_active_tokens(db)
            print(f"Active tokens check result: {len(active)}")
            # We created one valid future token, so it should be active
            # NOTE: SQLite datetime handling is tricky, it might return naive datetime
            # even if we stored aware. But as long as the query doesn't crash with
            # "can't subtract offset-naive and offset-aware datetimes", we fixed the bug.
            assert len(active) == 1
        except Exception as e:
            pytest.fail(f"get_active_tokens failed (likely timezone comparison issue): {e}")

    await engine.dispose()
