# Performance Benchmarks for R4 Verified Optimizations

This directory contains performance benchmarks for detecting regressions in R4 optimizations.

## Benchmark Structure

```
tests/performance/benchmarks_r4_verified/
├── conftest.py                  # Benchmark fixtures and utilities
├── bench_session_manager.py     # P0-2: Session Manager benchmarks
├── bench_token_blacklist.py     # P0-3: Token Blacklist benchmarks
├── bench_file_quota.py          # P0-4: File Quota benchmarks
└── baseline_metrics.json        # Baseline performance metrics
```

## Running Benchmarks

### Run All Benchmarks

```bash
# Run all benchmarks
uv run pytest tests/performance/benchmarks_r4_verified/ -v -m benchmark

# Run with detailed logging
uv run pytest tests/performance/benchmarks_r4_verified/ -v -s -m benchmark

# Run specific benchmark file
uv run pytest tests/performance/benchmarks_r4_verified/bench_session_manager.py -v -s
```

### Run Specific Benchmarks

```bash
# Session manager benchmarks
uv run pytest tests/performance/benchmarks_r4_verified/bench_session_manager.py::test_bench_session_count_o1 -v -s

# Token blacklist benchmarks
uv run pytest tests/performance/benchmarks_r4_verified/bench_token_blacklist.py::test_bench_token_blacklist_lua_script -v -s

# File quota benchmarks
uv run pytest tests/performance/benchmarks_r4_verified/bench_file_quota.py::test_bench_quota_check_redis_counter -v -s
```

## Benchmark Output

Each benchmark provides detailed statistics:

```
============================================================
Benchmark: session_count_o1_hlen
============================================================
Iterations: 100
Min:        0.234 ms
Max:        1.456 ms
Mean:       0.567 ms
Median:     0.543 ms
Stdev:      0.123 ms
P50:        0.543 ms
P95:        0.789 ms
P99:        0.912 ms
============================================================

✓ Threshold check passed: 0.567ms <= 1.0ms

============================================================
Baseline Comparison: session_count_o1_hlen
============================================================
Baseline:  0.550 ms
Current:   0.567 ms
Ratio:     1.03x
Change:    +3.1%
✓ No regression detected
============================================================
```

## Performance Thresholds

| Benchmark | Threshold | Baseline | Description |
|-----------|-----------|----------|-------------|
| session_count_o1_hlen | < 1ms | ~0.5ms | Count 100 sessions with HLEN |
| session_list_o1_hgetall | < 5ms | ~2ms | List 100 sessions with HGETALL |
| session_delete_o1_hdel | < 1ms | ~0.5ms | Delete session with HDEL |
| token_blacklist_lua_atomic | < 2ms | ~1ms | Blacklist token with Lua script |
| token_check_bloom_prefilter | < 0.1ms | ~0.05ms | Check token with Bloom filter |
| quota_check_o1_redis_get | < 1ms | ~0.5ms | Check quota with Redis GET |
| quota_increment_o1_redis_incr | < 1ms | ~0.5ms | Increment quota with INCR |

## Regression Detection

Benchmarks automatically detect performance regressions:

- **Threshold Check**: Fails if mean time exceeds threshold
- **Baseline Comparison**: Fails if >10% slower than baseline
- **Statistical Analysis**: Provides min/max/mean/p50/p95/p99

### Setting Baselines

To establish new baseline metrics:

```bash
# Run benchmarks and save results as baseline
uv run pytest tests/performance/benchmarks_r4_verified/ -v -s -m benchmark

# Manually edit baseline_metrics.json if needed
```

The baseline file (`baseline_metrics.json`) stores expected performance:

```json
{
  "session_count_o1_hlen": {
    "name": "session_count_o1_hlen",
    "iterations": 100,
    "mean_ms": 0.567,
    "p95_ms": 0.789,
    "p99_ms": 0.912
  }
}
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Performance Benchmarks

on: [push, pull_request]

jobs:
  benchmarks:
    runs-on: ubuntu-latest

    services:
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s

    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'

      - name: Install dependencies
        run: |
          pip install uv
          uv sync

      - name: Run benchmarks
        run: uv run pytest tests/performance/benchmarks_r4_verified/ -v -m benchmark
        env:
          REDIS_URL: redis://localhost:6379/0

      - name: Upload benchmark results
        uses: actions/upload-artifact@v3
        with:
          name: benchmark-results
          path: tests/performance/benchmarks_r4_verified/baseline_metrics.json
```

## Benchmark Details

### Session Manager (P0-2)

**Optimization**: O(N) list operations → O(1) Redis Hash operations

**Benchmarks**:
- `test_bench_session_count_o1`: Count sessions with HLEN
- `test_bench_session_list_o1`: List sessions with HGETALL
- `test_bench_session_delete_o1`: Delete session with HDEL
- `test_bench_session_exists_o1`: Check session with HEXISTS
- `test_bench_session_operations_comparison`: Compare Hash vs List

**Expected Improvement**: 10-100x faster for large session counts

### Token Blacklist (P0-3)

**Optimization**: 4 roundtrips → 1 Lua script + Bloom filter pre-filtering

**Benchmarks**:
- `test_bench_token_blacklist_lua_script`: Atomic blacklist with Lua
- `test_bench_token_check_with_bloom`: Bloom filter pre-filtering
- `test_bench_token_check_bloom_false_positive`: False positive handling
- `test_bench_bloom_filter_operations`: Bloom filter add/check speed
- `test_bench_blacklist_comparison_lua_vs_multi`: Lua vs multi-roundtrip

**Expected Improvement**: 4x faster (4 roundtrips → 1), Bloom filter ~1000x faster

### File Quota (P0-4)

**Optimization**: O(N) database aggregate → O(1) Redis counter

**Benchmarks**:
- `test_bench_quota_check_redis_counter`: Check quota with GET
- `test_bench_quota_increment`: Increment with INCR
- `test_bench_quota_decrement`: Decrement with DECR
- `test_bench_quota_check_with_limit`: Check if quota exceeded
- `test_bench_quota_atomic_increment_check`: Atomic increment + check
- `test_bench_quota_comparison_counter_vs_aggregate`: Counter vs aggregate
- `test_bench_quota_concurrent_operations`: Concurrent operations

**Expected Improvement**: 100-500x faster for large file counts

## Troubleshooting

### Redis Connection Errors

**Problem**: Benchmarks fail with Redis connection errors

**Solution**:
```bash
# Start Redis
docker-compose up -d redis

# Or use local Redis
redis-server

# Check connection
redis-cli ping
```

### Baseline File Missing

**Problem**: No baseline for comparison

**Solution**:
- First run establishes baseline automatically
- Or manually create `baseline_metrics.json`
- Baseline is optional; thresholds still enforced

### Benchmarks Too Slow

**Problem**: Benchmarks take too long

**Solution**:
- Reduce iterations in conftest.py (default: 100)
- Run specific benchmarks instead of all
- Use faster Redis instance (local vs Docker)

### Regression Detected

**Problem**: Benchmark fails with regression

**Solution**:
1. Verify the regression is real (run multiple times)
2. Check if baseline is outdated
3. Investigate code changes causing slowdown
4. Update baseline if new performance is acceptable

## Adding New Benchmarks

When adding new benchmarks:

1. **Follow naming convention**: `test_bench_<feature>_<operation>`
2. **Mark with `@pytest.mark.benchmark`**
3. **Set appropriate threshold**: Based on optimization goals
4. **Include comparison**: Show before/after improvement
5. **Log detailed stats**: Use `benchmark_runner` fixture
6. **Document in README**: Add to benchmark table

### Example Benchmark

```python
@pytest.mark.benchmark
def test_bench_my_optimization(benchmark_runner, baseline_comparator):
    """Benchmark my optimization.

    Verifies that operation is fast after optimization.
    """
    # Setup
    setup_test_data()

    # Benchmark
    def my_operation():
        # Operation to benchmark
        return perform_operation()

    result = benchmark_runner(
        name="my_optimization",
        func=my_operation,
        iterations=100,
        threshold_ms=1.0,  # Must be < 1ms
    )

    # Compare with baseline
    baseline_comparator(result)

    # Cleanup
    cleanup_test_data()
```

## Performance Targets

### P0 Optimizations (Critical)

- **Session Manager**: < 1ms for count, < 5ms for list (100 sessions)
- **Token Blacklist**: < 2ms for blacklist, < 0.1ms for Bloom check
- **File Quota**: < 1ms for quota check (10k files)

### Regression Threshold

- **10% slower**: Warning, investigate
- **>10% slower**: Fail, block merge

## Related Documentation

- [E2E Tests](../../e2e/r4_verified/README.md)
- [P0-2: Session Manager](../../../docs/optimizations/p0-2-session-manager.md)
- [P0-3: Token Blacklist](../../../docs/optimizations/p0-3-token-blacklist.md)
- [P0-4: File Quota](../../../docs/optimizations/p0-4-file-quota.md)
