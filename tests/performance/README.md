# PERF-R1-A: Performance Testing Suite

This directory contains performance testing infrastructure for the FastAPI SaaS project.

## Purpose

Establish measurable baselines before optimization (The "Science" Part).
Without baselines, we cannot prove changes move the needle.

## Directory Contents

| File | Purpose |
|------|---------|
| `locustfile.py` | Load test simulating realistic traffic patterns |
| `README.md` | This file - instructions and documentation |
| `baseline_*.log` | Baseline test results (generated) |
| `baseline_*.csv` | Locust metrics export (generated) |

## Traffic Distribution

The load test simulates realistic production patterns:

| Endpoint | Weight | Description |
|----------|--------|-------------|
| `GET /api/v1/users/me` | 80% | THE HOT PATH - every page load |
| `POST /api/v1/ai/chat` | 10% | AI completions |
| `POST /api/v1/auth/refresh` | 10% | Token refresh |

## Target Metrics

| Endpoint | Current p95 | Target p95 | Improvement |
|----------|-------------|------------|-------------|
| `/users/me` | ~50ms | <15ms | 70% ↓ |
| `/ai/chat` | ~200ms+ | <100ms | 50% ↓ |
| `/auth/refresh` | ~30ms | <10ms | 67% ↓ |

## Prerequisites

```bash
# Install locust
pip install locust

# Or with uv
uv pip install locust

# Create test user (if not exists)
# The test expects: perftest@example.com / TestPassword123!
```

## Quick Start

### 1. Web UI Mode (Recommended for first run)

```bash
# Start the API server
uv run uvicorn src.app.main:app --reload

# In another terminal, start Locust
locust -f tests/performance/locustfile.py --host http://localhost:8000

# Open http://localhost:8089 in your browser
# Set users: 10, spawn rate: 2
# Click "Start swarming"
```

### 2. Headless Mode (CI/Automated)

```bash
# Quick smoke test (10 users, 1 min)
locust -f tests/performance/locustfile.py \
    --headless \
    -u 10 \
    -r 2 \
    --run-time 1m \
    --host http://localhost:8000

# Full baseline (100 users, 5 min, export CSV)
locust -f tests/performance/locustfile.py \
    --headless \
    -u 100 \
    -r 10 \
    --run-time 5m \
    --host http://localhost:8000 \
    --csv=tests/performance/baseline
```

## Capturing Baselines

Before starting any optimization work, capture complete baselines:

```bash
# Navigate to project root
cd /path/to/FastAPI_saas

# 1. Capture test results
uv run pytest --tb=short 2>&1 | tee tests/performance/baseline_tests.log

# 2. Capture type check results
uv run mypy src/ 2>&1 | tee tests/performance/baseline_types.log

# 3. Capture lint results
uv run ruff check 2>&1 | tee tests/performance/baseline_lint.log

# 4. Run load test with CSV export
locust -f tests/performance/locustfile.py \
    --headless \
    -u 100 \
    -r 10 \
    --run-time 5m \
    --host http://localhost:8000 \
    --csv=tests/performance/baseline

# This creates:
# - baseline_stats.csv       (request statistics)
# - baseline_stats_history.csv (time series)
# - baseline_failures.csv    (error details)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PERF_TEST_EMAIL` | `perftest@example.com` | Test user email |
| `PERF_TEST_PASSWORD` | `TestPassword123!` | Test user password |
| `API_HOST` | `http://localhost:8000` | API base URL |

## Interpreting Results

### Locust Output

```
Name                          # reqs      # fails  |     Avg     Min     Max  Median  |   req/s
---------------------------------------------------------------------------------------------
GET /users/me                    1234       0(0%)  |      45      12     234      38  |   12.3
POST /ai/chat                     156       2(1%)  |     187      89     892     145  |    1.5
POST /auth/refresh                 98       0(0%)  |      28       8      67      24  |    0.9
```

### Key Metrics

- **p95 latency**: 95th percentile response time (most important)
- **p99 latency**: 99th percentile (worst case under normal load)
- **req/s**: Throughput (requests per second)
- **fail%**: Error rate (should be <1%)

### Success Criteria

After PERF-R1 optimizations:

| Metric | Threshold | Status |
|--------|-----------|--------|
| `/users/me` p95 | < 15ms | ⬜ Pending |
| `/ai/chat` p95 (DB portion) | < 100ms | ⬜ Pending |
| `/auth/refresh` p95 | < 10ms | ⬜ Pending |
| Error rate | < 1% | ⬜ Pending |
| All tests pass | 100% | ⬜ Pending |
| Type check clean | 0 errors | ⬜ Pending |
| Lint clean | 0 errors | ⬜ Pending |

## Troubleshooting

### "Connection refused"
- Ensure the API server is running on the specified host/port
- Check `--host` parameter matches your server

### "401 Unauthorized"
- Create the test user in the database
- Check `PERF_TEST_EMAIL` and `PERF_TEST_PASSWORD` environment variables

### Slow startup
- First request authenticates the user - this is expected
- Subsequent requests should be faster

### High error rate
- Check server logs for exceptions
- Verify database and Redis are running
- Check rate limiting configuration

## Next Steps

After capturing baselines:

1. Review `baseline_*.log` files for any existing issues
2. Analyze `baseline_stats.csv` for current performance
3. Proceed to Phase B optimizations (B1-B7)
4. Re-run load tests after each optimization
5. Compare against baselines to verify improvement

## Related Beads

- `PERF-R1-A` (FastAPI_saas-v7g): This phase
- `PERF-R1-B1` through `B7`: Optimization implementations
- `PERF-R1-C` (FastAPI_saas-85h): CI regression guardrails
