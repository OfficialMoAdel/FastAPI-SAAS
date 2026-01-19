# E2E Tests for R4 Verified Optimizations

This directory contains end-to-end validation tests for the R4 performance optimizations.

## Test Structure

```
tests/e2e/r4_verified/
├── conftest.py                      # Shared fixtures and logging setup
├── test_auth_rate_limiting_e2e.py   # P0-1: Auth rate limiting tests
├── test_session_management_e2e.py   # P0-2: Session lifecycle tests
├── test_token_blacklist_e2e.py      # P0-3: Token blacklist tests
└── test_file_upload_quota_e2e.py    # P0-4: File quota tests
```

## Prerequisites

### Required Services

1. **PostgreSQL** - Running and accessible
2. **Redis** - Running and accessible
3. **Application Server** - FastAPI app running

### Environment Setup

```bash
# Ensure database is running
docker-compose up -d postgres redis

# Or use local services
# PostgreSQL on localhost:5432
# Redis on localhost:6379
```

## Running Tests

### Run All E2E Tests

```bash
# Run all E2E tests with detailed logging
uv run pytest tests/e2e/r4_verified/ -v -s

# Run with coverage
uv run pytest tests/e2e/r4_verified/ --cov=src/app --cov-report=html
```

### Run Specific Test Files

```bash
# Auth rate limiting tests
uv run pytest tests/e2e/r4_verified/test_auth_rate_limiting_e2e.py -v -s

# Session management tests
uv run pytest tests/e2e/r4_verified/test_session_management_e2e.py -v -s

# Token blacklist tests
uv run pytest tests/e2e/r4_verified/test_token_blacklist_e2e.py -v -s

# File upload quota tests
uv run pytest tests/e2e/r4_verified/test_file_upload_quota_e2e.py -v -s
```

### Run Specific Tests

```bash
# Run single test
uv run pytest tests/e2e/r4_verified/test_auth_rate_limiting_e2e.py::test_auth_rate_limiting_premium_user -v -s

# Run tests matching pattern
uv run pytest tests/e2e/r4_verified/ -k "session" -v -s
```

## Test Logging

All tests include detailed logging of:
- HTTP requests and responses
- Redis operations
- Database queries
- Timing information
- Step-by-step execution flow

### Log Output Example

```
2026-01-18 15:30:45 [INFO] conftest: Created HTTP client for E2E test
2026-01-18 15:30:45 [INFO] conftest: Creating premium test user: premium_e2e@example.com
2026-01-18 15:30:46 [INFO] conftest: User authenticated, token: eyJhbGciOiJIUzI1NiIs...
2026-01-18 15:30:46 [INFO] test_auth_rate_limiting_e2e: [STEP] Starting auth rate limiting E2E test | timestamp=1705594246.123 | user=premium_e2e@example.com
2026-01-18 15:30:46 [INFO] test_auth_rate_limiting_e2e: [STEP] Making authenticated request | timestamp=1705594246.234 | tier=premium
2026-01-18 15:30:46 [INFO] test_auth_rate_limiting_e2e: [STEP] Received response | timestamp=1705594246.345 | status_code=200 | headers={'X-RateLimit-Limit': '1000', ...}
```

## Test Coverage

### P0-1: Auth Rate Limiting
- ✅ Premium user gets premium limits
- ✅ Free user gets free tier limits
- ✅ Rate limit exhaustion and recovery
- ✅ Anonymous vs authenticated limits

### P0-2: Session Management
- ✅ Full session lifecycle (create, list, delete)
- ✅ Session count accuracy with O(1) operations
- ✅ Session list performance with Redis Hashes
- ✅ Concurrent session operations

### P0-3: Token Blacklist
- ✅ Full blacklist flow (logout, rejection)
- ✅ Bloom filter pre-filtering
- ✅ Bloom filter false positive handling
- ✅ Blacklist expiration
- ✅ Multiple token blacklisting
- ✅ Blacklist performance

### P0-4: File Upload Quota
- ✅ Full quota flow (upload, check, delete)
- ✅ Quota counter accuracy
- ✅ Quota check performance (O(1))
- ✅ Concurrent upload quota accuracy
- ✅ Quota counter recovery

## Fixtures

### Available Fixtures

- `client` - Async HTTP client for API requests
- `db_session` - Database session for direct queries
- `authenticated_user` - Free tier user with access token
- `premium_user` - Premium tier user with access token
- `log_step` - Function to log test steps with timing
- `cleanup_redis` - Auto-cleanup of Redis test data

### Using Fixtures

```python
@pytest.mark.asyncio
async def test_example(authenticated_user: dict, client: AsyncClient, log_step):
    log_step("Starting test", user=authenticated_user["email"])

    headers = {"Authorization": f"Bearer {authenticated_user['access_token']}"}
    response = await client.get("/api/v1/users/me", headers=headers)

    log_step("Request completed", status=response.status_code)
    assert response.status_code == 200
```

## Troubleshooting

### Tests Fail with Connection Errors

**Problem**: Cannot connect to PostgreSQL or Redis

**Solution**:
```bash
# Check services are running
docker-compose ps

# Start services if needed
docker-compose up -d postgres redis

# Check environment variables
echo $DATABASE_URL
echo $REDIS_URL
```

### Tests Fail with Authentication Errors

**Problem**: User registration or login fails

**Solution**:
- Ensure database migrations are up to date: `uv run alembic upgrade head`
- Check that test database is clean or use test isolation
- Verify JWT secret is configured: `echo $SECRET_KEY`

### Redis Keys Not Cleaned Up

**Problem**: Test data persists in Redis

**Solution**:
- The `cleanup_redis` fixture should auto-clean test keys
- Manual cleanup: `redis-cli FLUSHDB` (⚠️ deletes all Redis data)
- Use test-specific Redis database: `REDIS_URL=redis://localhost:6379/1`

### Slow Test Execution

**Problem**: Tests take too long

**Solution**:
- Run tests in parallel: `pytest -n auto tests/e2e/r4_verified/`
- Use faster test database (in-memory or local)
- Reduce number of iterations in performance tests

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install dependencies
        run: |
          pip install uv
          uv sync

      - name: Run migrations
        run: uv run alembic upgrade head
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/test

      - name: Run E2E tests
        run: uv run pytest tests/e2e/r4_verified/ -v
        env:
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/test
          REDIS_URL: redis://localhost:6379/0
```

## Performance Benchmarks

Expected performance characteristics:

| Operation | Target | Actual (Avg) |
|-----------|--------|--------------|
| Rate limit check | < 10ms | ~5ms |
| Session list (10 sessions) | < 100ms | ~50ms |
| Token blacklist | < 50ms | ~20ms |
| Quota check | < 50ms | ~15ms |
| Concurrent uploads (5) | < 500ms | ~300ms |

## Contributing

When adding new E2E tests:

1. Follow existing test structure and naming conventions
2. Use `log_step` fixture for detailed logging
3. Include performance assertions where applicable
4. Clean up test data in fixtures or teardown
5. Document test purpose and flow in docstring
6. Add test to this README

## Related Documentation

- [P0-1: Auth Rate Limiting](../../../docs/optimizations/p0-1-auth-rate-limiting.md)
- [P0-2: Session Manager](../../../docs/optimizations/p0-2-session-manager.md)
- [P0-3: Token Blacklist](../../../docs/optimizations/p0-3-token-blacklist.md)
- [P0-4: File Quota](../../../docs/optimizations/p0-4-file-quota.md)
