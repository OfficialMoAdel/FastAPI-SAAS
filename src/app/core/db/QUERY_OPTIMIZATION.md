# Query Optimization Guide

This guide documents optimized query patterns for the FastAPI SaaS application to prevent performance regression and N+1 problems.

## Table of Contents

1. [N+1 Prevention](#n1-prevention)
2. [Index Usage](#index-usage)
3. [Pagination Best Practices](#pagination-best-practices)
4. [Async SQLAlchemy Patterns](#async-sqlalchemy-patterns)
5. [Monitoring](#monitoring)

---

## N+1 Prevention

### Understanding Lazy vs Eager Loading

**Lazy Loading** (default in SQLAlchemy 2.0):
- Related objects are loaded only when accessed
- Can cause N+1 queries if iterating through a collection
- Good for: Rarely accessed relationships, large datasets

**Eager Loading**:
- Loads related objects in the same query or additional query
- Prevents N+1 problems
- Good for: Frequently accessed relationships, small datasets

### When to Use `joinedload` (One-to-One)

Use `joinedload` for one-to-one or many-to-one relationships where you always need the related object.

```python
from sqlalchemy.orm import joinedload

# BAD: N+1 query problem
users = await session.execute(select(User))
for user in users.scalars():
    print(user.tenant.name)  # Executes a query for each user!

# GOOD: Single query with JOIN
users = await session.execute(
    select(User)
    .options(joinedload(User.tenant))  # Load tenant relationship
)
for user in users.scalars():
    print(user.tenant.name)  # No additional queries!
```

**Best Practices:**
- Use for relationships you'll access in every iteration
- Avoid for deep nested relationships (can cause Cartesian product)
- Consider `selectinload` for one-to-many relationships

### When to Use `selectinload` (One-to-Many)

Use `selectinload` for one-to-many relationships to avoid Cartesian products.

```python
from sqlalchemy.orm import selectinload

# BAD: N+1 query problem
tenant = await session.execute(
    select(Tenant).where(Tenant.id == tenant_id)
).scalar_one()
for user in tenant.users:  # Executes a query for each user!
    print(user.email)

# GOOD: Two queries (tenant + users)
tenant = await session.execute(
    select(Tenant)
    .where(Tenant.id == tenant_id)
    .options(selectinload(Tenant.users))  # Load users relationship
).scalar_one()
for user in tenant.users:  # No additional queries!
    print(user.email)
```

**Best Practices:**
- Use for one-to-many relationships
- Better than `joinedload` for large collections
- Consider `contains_eager` for already loaded relationships

### Example: Preventing N+1 in API Endpoints

```python
from fastapi import Depends
from sqlalchemy.orm import selectinload, joinedload

async def get_tenant_users(
    tenant_id: str,
    session: AsyncSession = Depends(get_db),
):
    """Get all users for a tenant with their roles and settings."""
    tenant = await session.execute(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .options(
            selectinload(Tenant.users)  # Load users
            .selectinload(Tenant.users).selectinload(User.role)  # Load user roles
            .joinedload(Tenant.settings)  # Load tenant settings
        )
    ).scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return tenant
```

---

## Index Usage

### When to Add Indexes

Add indexes when:
- Columns are frequently used in WHERE clauses
- Columns are used in JOIN conditions
- Columns are used in ORDER BY clauses
- Columns have high cardinality (many unique values)

**Don't index:**
- Columns with low cardinality (e.g., boolean flags)
- Columns that are frequently updated
- Tables that are very small (< 100 rows)

### Partial Index Examples

Partial indexes are more efficient and smaller than full indexes.

```python
# Index only active users
class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Partial index for active users only
    __table_args__ = (
        Index("idx_user_active", "is_active", postgresql_where=is_active == True),
    )
```

```python
# Index for deleted_at is null (common pattern)
class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Partial index for non-deleted posts
    __table_args__ = (
        Index("idx_posts_not_deleted", "deleted_at", postgresql_where=deleted_at.is_(None)),
    )
```

### Composite Index Strategies

Composite indexes are useful for queries with multiple conditions.

```python
# Index for (tenant_id, created_at) queries
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Composite index for tenant filtering and date sorting
    __table_args__ = (
        Index("idx_audit_logs_tenant_created", "tenant_id", "created_at"),
    )
```

**Query Optimization:**
```python
# Uses composite index
logs = await session.execute(
    select(AuditLog)
    .where(AuditLog.tenant_id == tenant_id)
    .order_by(AuditLog.created_at.desc())
    .limit(10)
)
```

---

## Pagination Best Practices

### Offset Pagination vs Keyset Pagination

**Offset Pagination** (simple but slow for large offsets):
```python
# Simple but slow for large pages
page = page_size * (page_num - 1)
users = await session.execute(
    select(User)
    .order_by(User.created_at)
    .offset(page)
    .limit(page_size)
)
```

**Problems:**
- `COUNT(*)` is slow on large tables
- Database must scan and discard all rows before offset
- Performance degrades as page number increases

**Keyset Pagination** (cursor-based, fast):
```python
# Fast and scalable
last_id = request.query_params.get("last_id")  # Cursor from previous page

query = select(User).order_by(User.id).limit(page_size)

if last_id:
    query = query.where(User.id > int(last_id))

users = await session.execute(query)
```

**Benefits:**
- No `COUNT(*)` query needed
- Constant time regardless of page number
- Works well with infinite scroll

### Why `COUNT(*)` is Slow on Large Tables

```python
# BAD: Slow on large tables
total = await session.execute(
    select(func.count(User.id))
).scalar()

# GOOD: Use approximate count or skip count
# Option 1: Use EXPLAIN (PostgreSQL)
result = await session.execute(
    text("EXPLAIN SELECT * FROM users")
)
# Parse estimated rows from EXPLAIN output

# Option 2: Skip count, just fetch next page
users = await session.execute(
    select(User)
    .order_by(User.id)
    .limit(page_size)
)
has_more = len(users.scalars().all()) == page_size
```

---

## Async SQLAlchemy Patterns

### Streaming Results for Large Datasets

Use `yield_per` to stream results instead of loading everything into memory.

```python
from sqlalchemy import text

# BAD: Loads all users into memory
users = await session.execute(select(User))
for user in users.scalars():
    process_user(user)  # All users loaded at once

# GOOD: Streams users in batches
async for user in session.execute(
    select(User).yield_per(100)  # Load 100 users at a time
):
    process_user(user)  # Users loaded in batches
```

### Bulk Insert/Update Operations

Use bulk operations instead of individual inserts/updates.

```python
# BAD: N individual INSERT statements
for user_data in user_list:
    user = User(**user_data)
    session.add(user)
    await session.commit()

# GOOD: Single bulk INSERT
users = [User(**data) for data in user_list]
session.add_all(users)
await session.commit()

# EVEN BETTER: Use core bulk insert
from sqlalchemy import insert

stmt = insert(User).values(user_list)
await session.execute(stmt)
await session.commit()
```

**Bulk Update:**
```python
# BAD: N individual UPDATE statements
for user in users:
    user.last_login = datetime.now()
    await session.commit()

# GOOD: Single bulk UPDATE
from sqlalchemy import update

stmt = (
    update(User)
    .where(User.id.in_([u.id for u in users]))
    .values(last_login=datetime.now())
)
await session.execute(stmt)
await session.commit()
```

---

## Monitoring

### Reading Query Logs

Enable query logging in development:

```python
# src/app/core/config.py
import logging

logging.basicConfig()
logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
```

**Log Output:**
```
INFO:sqlalchemy.engine.Engine:SELECT user.id, user.email FROM user WHERE user.tenant_id = 1
INFO:sqlalchemy.engine.Engine:[generated in 12ms]
```

### Using `EXPLAIN ANALYZE`

Analyze query performance in PostgreSQL:

```python
from sqlalchemy import text

result = await session.execute(
    text("EXPLAIN ANALYZE SELECT * FROM users WHERE tenant_id = 1")
)
print(result.all())
```

**Output Analysis:**
```
Index Scan using idx_user_tenant_id on users  (cost=0.42..8.44 rows=1 width=123)
  Index Cond: (tenant_id = 1)
  Planning Time: 0.123 ms
  Execution Time: 0.456 ms
```

**Key Metrics:**
- `Index Scan`: Good, using an index
- `Seq Scan`: Bad, full table scan
- `Execution Time`: Total query time
- `rows`: Number of rows processed

### Monitoring N+1 Queries

Use SQLAlchemy's `echo` mode to detect N+1 problems:

```python
# Enable query logging
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # Log all queries
    echo_pool=True,
)
```

**Red Flags:**
- Multiple similar queries in a loop
- Same query executed N times with different IDs
- Queries executed inside iteration loops

---

## Quick Reference

| Pattern | Use Case |
|---------|----------|
| `joinedload` | One-to-one, always accessed |
| `selectinload` | One-to-many, avoid N+1 |
| `contains_eager` | Already loaded relationships |
| `yield_per` | Streaming large datasets |
| Bulk operations | Multiple inserts/updates |
| Partial indexes | Filtered data subsets |
| Composite indexes | Multi-column queries |
| Keyset pagination | Large datasets, infinite scroll |

---

## Further Reading

- [SQLAlchemy 2.0 Documentation](https://docs.sqlalchemy.org/en/20/)
- [PostgreSQL Index Types](https://www.postgresql.org/docs/current/indexes-types.html)
- [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)
- [FastAPI Performance Tips](https://fastapi.tiangolo.com/tutorial/dependencies/)