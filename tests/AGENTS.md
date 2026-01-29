# FastAPI SaaS Test Suite - Agent Guidance

## Package Identity
Comprehensive test suite for the FastAPI SaaS application using pytest with async support, database fixtures, and API endpoint testing.

## Setup & Run
```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/app --cov-report=html

# Run specific test file
uv run pytest tests/test_users.py -v

# Run with verbose output
uv run pytest -v

# Run only unit tests
uv run pytest tests/unit/ -v

# Run only integration tests
uv run pytest tests/integration/ -v
```

## Patterns & Conventions

### Test Structure
✅ DO: Organize tests by feature with clear naming
```python
# tests/test_users.py (lines 1-30)
class TestUserCreation:
    """Test user creation endpoints and logic."""
    
    async def test_create_user_success(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Test successful user creation."""
        user_data = {
            "email": "test@example.com",
            "username": "testuser",
            "password": "TestPassword123!",
        }
        
        response = await client.post("/api/v1/users/", json=user_data)
        assert response.status_code == 201
        
        data = response.json()
        assert data["email"] == user_data["email"]
        assert data["username"] == user_data["username"]
        assert "password" not in data
```

### Async Testing
✅ DO: Use async test functions with proper fixtures
```python
# All tests must be async
async def test_async_operation(self, db_session: AsyncSession) -> None:
    # Test async database operations
    user = await crud_users.create(db=db_session, object=user_data)
    assert user is not None
```

❌ DON'T: Use sync test functions for async operations

### Database Fixtures
✅ DO: Use database session fixtures for test isolation
```python
# tests/conftest.py (lines 1-50)
@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with database session."""
    app.dependency_overrides[get_db_session] = lambda: db_session
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
```

### Test Data Management
✅ DO: Use fixtures for test data
```python
# tests/helpers/generators.py (lines 1-30)
@pytest.fixture
def user_create_data() -> dict[str, Any]:
    """Generate valid user creation data."""
    return {
        "email": "test@example.com",
        "username": "testuser",
        "password": "TestPassword123!",
    }

@pytest.fixture
async def test_user(
    user_create_data: dict[str, Any], db_session: AsyncSession
) -> dict[str, Any]:
    """Create a test user in database."""
    return await user_service.create_user(
        user=UserCreate(**user_create_data), db=db_session
    )
```

### API Testing
✅ DO: Test API endpoints with proper HTTP status codes
```python
async def test_get_user_success(
    self, client: AsyncClient, test_user: dict[str, Any]
) -> None:
    """Test getting a user by ID."""
    response = await client.get(f"/api/v1/users/{test_user['id']}")
    assert response.status_code == 200
    
    data = response.json()
    assert data["id"] == test_user["id"]
    assert data["email"] == test_user["email"]
```

### Error Testing
✅ DO: Test error conditions and edge cases
```python
async def test_create_user_duplicate_email(
    self, client: AsyncClient, test_user: dict[str, Any]
) -> None:
    """Test creating user with duplicate email."""
    user_data = {
        "email": test_user["email"],  # Duplicate email
        "username": "newuser",
        "password": "TestPassword123!",
    }
    
    response = await client.post("/api/v1/users/", json=user_data)
    assert response.status_code == 409
    
    data = response.json()
    assert "already registered" in data["detail"].lower()
```

## Touch Points / Key Files

### Test Configuration
- Conftest: `tests/conftest.py` - Pytest fixtures and configuration
- Helpers: `tests/helpers/` - Test utilities and data generators

### Test Files
- User Tests: `tests/test_users.py` - User endpoint tests
- Auth Tests: `tests/test_auth.py` - Authentication tests
- Integration Tests: `tests/integration/` - End-to-end tests

### Test Utilities
- Generators: `tests/helpers/generators.py` - Test data generators
- Mocks: `tests/helpers/mocks.py` - Mock objects and services

## JIT Index Hints

### Test Searches
```bash
# Find all test files
find tests/ -name "*.py" -type f

# Find test classes
rg -n "class Test.*:" tests/

# Find async tests
rg -n "async def test_.*:" tests/

# Find test fixtures
rg -n "@pytest\.fixture" tests/

# Find API tests
rg -n "client\.(get|post|put|delete)" tests/
```

### Test Coverage
```bash
# Generate coverage report
uv run pytest --cov=src/app --cov-report=html

# Check coverage for specific module
uv run pytest --cov=src.app.modules.users tests/test_users.py
```

## Common Gotchas

### Test Isolation
✅ DO: Use fresh database session for each test
❌ DON'T: Share database state between tests

### Async Testing
✅ DO: All database tests must be async
```python
async def test_database_operation(self, db_session: AsyncSession):
    user = await crud_users.create(db=db_session, object=user_data)
```

❌ DON'T: Use sync database operations in tests

### Authentication Testing
✅ DO: Test both authenticated and unauthenticated endpoints
```python
async def test_unauthorized_access(self, client: AsyncClient):
    response = await client.get("/api/v1/users/protected")
    assert response.status_code == 401

async def test_authorized_access(
    self, client: AsyncClient, authenticated_user: dict
):
    response = await client.get("/api/v1/users/protected")
    assert response.status_code == 200
```

### Test Data
✅ DO: Use fixtures for reusable test data
❌ DON'T: Hardcode test data in test methods

### Error Testing
✅ DO: Test all error conditions
❌ DON'T: Only test happy path scenarios

## Pre-PR Checks

### Test Execution
```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/app --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_users.py -v

# Run only failed tests
uv run pytest --lf
```

### Test Quality
```bash
# Check test coverage (aim for >80%)
uv run pytest --cov=src/app --cov-fail-under=80

# Run tests with type checking
uv run mypy tests/

# Lint test files
uv run ruff check tests/
```

### Database Testing
```bash
# Test database migrations
uv run pytest tests/test_migrations.py -v

# Test database models
uv run pytest tests/test_models.py -v
```

## Phase 1 Security Testing

### Log Sanitizer Unit Tests
Location: `tests/unit/core/test_log_sanitizer.py`

Purpose: Test the log sanitization system that strips sensitive data from logs.

Run:
```bash
uv run pytest tests/unit/core/test_log_sanitizer.py -v
```

Test Coverage:
- JWT token sanitization (pattern: eyJ[0-9a-zA-Z_-]+\.[0-9a-zA-Z_-]+\.[0-9a-zA-Z_-]+)
- API key redaction (api_key, secret, password, token)
- Email masking (u***@domain.com)
- Credit card number sanitization
- Database URL credential stripping
- Cloud service key redaction (AWS, Azure, GCP)
- Nested dictionary and list sanitization

Expected Results: 17 tests, 100% pass rate

### Log Sanitization E2E Tests
Location: `tests/e2e/test_log_sanitization_e2e.py`

Purpose: End-to-end verification that real log entries are sanitized correctly.

Run:
```bash
uv run pytest tests/e2e/test_log_sanitization_e2e.py -v
```

Test Coverage:
- Real log entry verification
- Structured logging integration
- Multi-value sanitization
- Edge case handling

Expected Results: 5 tests, 100% pass rate

### RLS Integration Tests
Location: `tests/integration/test_rls_isolation.py`

Purpose: Verify Row Level Security policies enforce tenant isolation.

Run:
```bash
uv run pytest tests/integration/test_rls_isolation.py -v
```

Test Coverage:
- Cross-tenant query rejection (fail-closed)
- Same-tenant data access
- Super-admin bypass with audit logging
- Background job context propagation
- Middleware integration

Expected Results: 13 tests, 100% pass rate

### RLS Performance Tests
Location: `tests/performance/test_rls_performance.py`

Purpose: Measure RLS query overhead compared to baseline.

Run:
```bash
# First, capture baseline without RLS
uv run pytest tests/performance/baseline_pre_rls.py -v

# Then run RLS performance tests
uv run pytest tests/performance/test_rls_performance.py -v
```

Metrics:
- Query overhead: < 5% target, < 10% acceptable
- Index usage verification
- Query plan analysis

Expected Results: 6 benchmark tests, overhead within acceptable limits

### Performance Testing
```bash
# Run tests with timing
uv run pytest --durations=10

# Run tests in parallel
uv run pytest -n auto
```

## Phase 1 Security Testing

### Log Sanitizer Tests
Location: `tests/unit/core/test_log_sanitizer.py`

Purpose: Verify sensitive data is stripped from logs before output.

Run tests:
```bash
uv run pytest tests/unit/core/test_log_sanitizer.py -v
```

Test coverage:
- JWT token sanitization (pattern: `eyJ`)
- API key redaction (values masked as `[REDACTED]`)
- Email address masking (format: `u***@domain.com`)
- Database URL sanitization
- Credit card number masking
- Cloud provider key redaction

E2E Verification:
```bash
uv run pytest tests/e2e/test_log_sanitization_e2e.py -v
```

### Row Level Security (RLS) Tests
Location: `tests/integration/test_rls_isolation.py`

Purpose: Verify database-level tenant isolation works correctly.

Prerequisites:
- PostgreSQL database with RLS migration applied
- Multiple test tenants created

Run tests:
```bash
uv run pytest tests/integration/test_rls_isolation.py -v
```

Test scenarios:
- Cross-tenant queries return empty results (fail-closed)
- Tenant context propagation through middleware
- Background job tenant isolation
- Super-admin bypass functionality
- Audit logging for bypass events

Performance Benchmarking:
```bash
# Baseline (before RLS)
uv run python tests/performance/baseline_pre_rls.py

# RLS performance
uv run pytest tests/performance/test_rls_performance.py -v
```

Acceptable overhead: < 5% for all query types

### RLS Helper Module Tests
Location: `tests/unit/core/db/test_rls.py`

Purpose: Test RLS context management utilities.

Run tests:
```bash
uv run pytest tests/unit/core/db/test_rls.py -v
```

Test coverage:
- `set_rls_context()` sets session variables
- `clear_rls_context()` resets session variables
- `rls_context()` async context manager
- `bypass_rls()` admin bypass with logging