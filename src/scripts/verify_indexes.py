import asyncio
from sqlalchemy import text
from app.core.db.database import async_get_db
from app.core.config import settings

async def verify():
    # We need a session. simpler to just make an engine.
    from sqlalchemy.ext.asyncio import create_async_engine
    
    url = f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    engine = create_async_engine(url)
    
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename IN ('roles', 'role_permissions', 'user_roles')"))
        indexes = [row[0] for row in result.fetchall()]
        
        expected = [
            'ix_roles_tenant_id',
            'ix_role_permissions_permission_id',
            'ix_user_roles_tenant_id',
            'ix_user_roles_role_id'
        ]
        
        print("Found indexes:", indexes)
        
        missing = [idx for idx in expected if idx not in indexes]
        
        if missing:
            print(f"ERROR: Missing indexes: {missing}")
            exit(1)
        else:
            print("SUCCESS: All expected indexes found.")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(verify())
