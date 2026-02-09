# Baseline Metrics Report

**Date:** 2026-02-09
**Environment:** Local Development (Windows/SQLite)

## Executive Summary
This report establishes the performance and resource usage baselines for Phase 0, prior to applying optimizations for wasted connection removal (Phase 1) and RLS fixes (Phase 2).

## 1. Middleware Performance (Micro-benchmarks)
*Source: `tests/benchmarks/middleware_profiler.py`*

Key critical path operations latency (mean):

| Operation | Latency (µs) | Notes |
| :--- | :--- | :--- |
| **JWT Decode** | 30.27 | Most expensive operation (CPU bound) |
| **IP Network Check** | 7.88 | Trusted proxy verification |
| **JSON Serialization** | 7.48 | Tenant data caching cost |
| **Path Exemption** | 0.35 | Very fast (set lookup) |

**Analysis:**
- JWT decoding dominates the CPU time for middleware.
- IP checks are relatively expensive due to network parsing.
- Path exemption is highly optimized (frozenset).

## 2. Tenant Context Memory Usage
*Source: `tests/benchmarks/bench_tenant_context.py`*

Memory footprint of the multi-tenancy context switching:

| Scenario | Memory Usage | Threshold | Status |
| :--- | :--- | :--- | :--- |
| **1000 Dict Entries** | 297 KB | < 350 KB | Baseline Established |
| **Concurrent ContextVars** | 8.45 KB | < 15 KB | Efficient |

**Analysis:**
- The current implementation of `TenantContext` using `ContextVar` is memory efficient.
- Dictionary storage for tenant attributes scales linearly but remains within reasonable limits.

## 3. RLS Performance
*Source: `tests/performance/test_rls_performance.py`*

**Status: SKIPPED**
- Reason: Benchmark requires PostgreSQL-specific syntax (`SET LOCAL`) but test environment is using SQLite.
- Action: Will be baselined in CI/CD environment with real PostgreSQL service.

## 4. Load Testing
*Source: `tests/load_testing/locustfile.py`*

**Status: READY**
- Configuration: `tests/load_testing/config.py` updated with profiles (smoke, stress).
- Scenarios: Login, Profile Fetch, File Upload (Mocked), Audit Logs.
- Note: System-wide load test baseline requires running application server.
