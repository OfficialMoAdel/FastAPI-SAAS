# Performance Baselines

This directory stores baseline performance metrics for comparison during optimization work.

## File Naming Convention

- `baseline_perf_r{N}.json` - Round N performance metrics (p50/p95/p99 latencies)
- `baseline_load_r{N}.json` - Round N load test results
- `baseline_flamegraph_r{N}.svg` - Round N CPU flame graph from py-spy

## Baseline Format

Each `baseline_perf_r{N}.json` contains:

```json
{
  "metadata": {
    "captured_at": "ISO timestamp",
    "git_commit": "short hash",
    "git_branch": "branch name",
    "python_version": "3.x.x",
    "machine": "CPU info"
  },
  "benchmarks": {
    "jwt_token_creation": {
      "mean_ms": 0.022,
      "median_ms": 0.017,
      "p50_ms": 0.017,
      "p95_ms": 0.025,
      "p99_ms": 0.035,
      "ops_per_sec": 45000
    }
  },
  "thresholds": {
    "users_me_ms": 15,
    "auth_refresh_ms": 10
  }
}
```

## Usage

1. **Capture new baseline**: `python scripts/capture_baseline.py --round 1`
2. **Compare with baseline**: `python scripts/compare_baseline.py --baseline r1 --current r2`

## Current Baselines

| Round | Date | Commit | Purpose |
|-------|------|--------|---------|
| R1 | 2026-01-13 | eda0e09 | Pre-optimization baseline |
