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

### 1. Install locust

```bash
pip install locust      # or: uv pip install locust
```

### 2. Seed one account per virtual user

The backend enforces `auth.AUTH_MAX_SESSIONS` (default **5**) concurrent sessions per
user, with oldest-session eviction on every login/refresh. If every virtual user shares
one account, each login revokes a peer's session and `/users/me` answers
`401 Session has been revoked` — a ~100% failure rate that has nothing to do with
performance. Seed a pool instead:

```bash
python scripts/setup_locust_test_data.py          # 100 accounts: perf_user_<i>@example.com
PERF_TEST_USER_COUNT=200 python scripts/setup_locust_test_data.py
```

Credentials are resolved by `PERF_TEST_EMAILS` (comma-separated, highest priority) or
`PERF_TEST_USER_COUNT` (default 100). The seeding script uses the same resolution order.

### 3. Start the server under PRODUCTION limits, on its own host

All of these defaults are per client IP or per user, and every virtual user on a
load host shares one IP. **Eight** controls gate a measured request, not four —
the four that were documented for years are rows 1-4 below, and rows 5-8 are
the ones that void runs while every documented budget looks relaxed:

| # | Setting | Default | Effect |
|---|---|---|---|
| 1 | `SECURITY_BUDGETS["auth"]` | `(5, 60)` | 5 logins/min |
| 2 | `DEFAULT_ANONYMOUS_LIMIT` | `(10, 3600)` | 10 logins/hour (login is anonymous) |
| 3 | `AUTO_BAN_ENABLED` | `true`, threshold 10 | bans the IP 1h, escalating to 2h |
| 4 | `DEFAULT_AUTHENTICATED_LIMIT` | `(50, 3600)` | 50/hour on authenticated paths |
| 5 | `ANONYMOUS_IP_AGGREGATE_LIMIT` | `(300, 3600)` | 300 distinct per-path keys/hour from one IP — **not** part of `SECURITY_BUDGETS`, and keyed on the **peer** address, so per-user client IPs do not spread it |
| 6 | `TIER_RATE_LIMITS` | `free` 10/h, `basic` 100/h, `pro` 1000/h, `enterprise` 10000/h | **per-USER-per-path** — spreading load across 300 accounts does not spread it; each account hits its own ceiling on the hot path |
| 7 | `DEFAULT_RATE_LIMIT_LIMIT` | `100` | fallback when no tier and no per-path policy matches. A plain int — `"[N,S]"` is a startup error |
| 8 | `DEFAULT_RATE_LIMIT_PERIOD` | `3600` | fallback window. Also a plain int |

A capacity number measured with those limits relaxed is not a capacity number. So the
script does **not** relax them, and it does **not** start the server. You start the app
yourself on a separate host, exactly as the image does:

```bash
# on the APP HOST, exactly the Dockerfile CMD — no --loop, no --http,
# so uvicorn resolves uvloop + httptools itself
uvicorn src.app.main:app --host 0.0.0.0 --port 8000
```

If you deliberately measure a run with the limits relaxed — a rate-limit smoke test,
not a baseline — override them in the server's process env and expect the artefact to
say so. The app-host report records the effective values, and `AUTO_BAN_ENABLED=false`
becomes a label in the manifest. Relaxing only rows 1-3 is the mistake that cost two
matrix runs: **relax all eight or the run is void**:

```bash
export AUTO_BAN_ENABLED=false
export SECURITY_BUDGETS='{"auth":[10000,60],"refresh":[10000,60],"ai_chat":[10000,60]}'
export DEFAULT_ANONYMOUS_LIMIT="[100000,3600]"
export DEFAULT_AUTHENTICATED_LIMIT="[100000,3600]"
export ANONYMOUS_IP_AGGREGATE_LIMIT="[1000000,3600]"
export TIER_RATE_LIMITS='{"free":[100000,3600],"basic":[100000,3600],"pro":[100000,3600],"enterprise":[100000,3600]}'
export DEFAULT_RATE_LIMIT_LIMIT=100000
export DEFAULT_RATE_LIMIT_PERIOD=3600
```

Rows 7-8 are ints, so they are **not** JSON-quoted; rows 1-6 are JSON. Every one of
these is recorded by `scripts/record_config_disclosure.py`, and §4.5 of the plan
voids a run in which any of them was left at a load-producing value.

These are process-env overrides — `src/.env` is never modified. If an IP does get
banned, clear it in Redis: `security:ban:<ip>` and `security:ban_count:<ip>`.

### 4. Bypass the HTTP proxy (if your host sets one)

If `http_proxy` is set, both curl and Locust (requests honours it) will route localhost
traffic through it and fail. Use `--noproxy '*'` / `NO_PROXY='*'`, and prefer
`127.0.0.1` over `localhost`.

### 5. Ollama — only if you want to measure `/ai/chat`

The AI task is **disabled by default** (`PERF_TEST_AI_ENABLED=0`) because without a
running Ollama every call blocks for seconds and then 503s, which inflates the tail
latency of every other endpoint. Enable it once Ollama is up:

```bash
PERF_TEST_AI_ENABLED=1 ./scripts/run_perf_baseline.sh
```

## Quick Start

### 1. Scripted baseline (recommended)

Three steps across two hosts. The load host refuses to generate load against a server on
itself, because gevent's hub and Locust's CSV aggregation competing with the app for CPU
makes every latency figure a contention measurement rather than a server measurement.

```bash
# 1. APP HOST — record what the server resolves to, and WHERE it runs. Run from
#    the same checkout and the same image as the server itself. Two facts about
#    the deployment cannot be read from inside the process and no in-repo file
#    records either, so they are declared: the image digest and the orchestrator.
#    PERF_STORE_LOCALITY is only needed when the store addresses are bare
#    hostnames (they usually are); a loopback or routable IP literal is derived.
#    Any of the six fields left unrecorded STOPS the run — a manifest missing the
#    core count reads as complete and is not.
PERF_IMAGE_DIGEST=sha256:... \
PERF_ORCHESTRATOR=docker-compose \
PERF_STORE_LOCALITY=remote \
  python -m src.benchmarks.run_manifest --emit-app-host-report > app_host_report.json
scp app_host_report.json LOAD_HOST:reports/

# 2. APP HOST — start the server under production limits
uvicorn src.app.main:app --host 0.0.0.0 --port 8000

# 3. LOAD HOST — measure in three phases. --host is required; there is no default.
./scripts/run_perf_baseline.sh --host http://app-host.internal:8000
./scripts/run_perf_baseline.sh --host http://app-host.internal:8000 -u 20 -r 5 -t 1m
./scripts/run_perf_baseline.sh --host http://app-host.internal:8000 --seed
```

The run is three separate locust processes, each writing its own CSVs: a **cold**
window (`--cold-window`, default 30s, `..._cold_*`), a **hold** (`--hold`, default
the 60s floor the harness declares, `..._hold_*`), and the **measured** phase
(`-t`, `--reset-stats`). The hold is not a reset that might come early — it is a
different process writing different files, so its samples are structurally
incapable of reaching a reported percentile. `--hold` shorter than the floor is
REFUSED rather than clamped, and a phase whose CSVs are missing aborts the run.

`reports/baseline_<timestamp>_phases.json` then records cold and warm **per
endpoint**, side by side, with the ratio between them — the cold numbers are
5-10x the warm ones, so reporting only one of them is what let that inflation hide
in the first place. The script also writes `locust_baseline_<timestamp>.log` in the
repo root, CSVs in `reports/`, and two manifests: one at run start and one at run
end. The end manifest is written only if every invariant section (git, harness,
python, load host, stores, limits, labels, app host, run params) is byte-identical
to the start; a run whose configuration moved while it was in flight is void and
the end record is not written.

### 2. Web UI Mode

```bash
# Start the API server on the app host (Prerequisite 3), then:
locust -f tests/performance/locustfile.py --host http://app-host.internal:8000
# Open http://localhost:8089, set users: 10, spawn rate: 2, click "Start swarming"
```

Keep the user count at or below the seeded pool size, and under the login rate limit.

### 3. Headless (manual)

```bash
export NO_PROXY='*' no_proxy='*'
locust -f tests/performance/locustfile.py \
    --headless -u 100 -r 10 --run-time 5m \
    --host http://127.0.0.1:8000 \
    --csv=reports/baseline
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

## Profiling `/account/refresh`

Locust gives percentiles; it does not say *why*. `scripts/profile_refresh.py`
attributes the milliseconds. It boots the app in-process and drives it over
ASGI, so no TCP stack and no proxy latency pollutes the numbers — what is left
is app + DB + Redis.

```bash
python scripts/profile_refresh.py                 # 50 refreshes, 5 warmup
python scripts/profile_refresh.py -n 100 --warm 10
python scripts/profile_refresh.py --json reports/refresh_profile.json
```

Probes (all installed by monkeypatch — nothing in `src/` is modified):

- `AsyncSession.execute` / `commit` / `flush`, plus SQLAlchemy cursor events so
  the INSERTs that ORM `add()` hides inside `commit()` are visible too.
- Redis command execution, patched at the driver level so every client
  (`cache_client`, `session_manager`, `jwt_service`) is covered.
- Named phases of the refresh path, with a phase stack so each DB/Redis event is
  attributed to the phase that caused it.

Two gotchas that cost time:

- The IP-filter middleware rejects the default `python-httpx` UA as a bad bot
  (403), so the script sends a browser UA.
- The auth budget stops the run at 5 refreshes unless the server's process env is
  overridden (`SECURITY_BUDGETS` etc.). That makes the run a rate-limit measurement
  rather than a baseline, so `run_perf_baseline.sh` does not do it for you.

### Result — 2026-09-22 (PERF-R1-B1)

Before → after, in-process, 40 samples:

| | before | after |
|---|---:|---:|
| total per request | 15.45 ms | **10.73 ms** |
| `blacklist_token` | 7.00 ms | 2.96 ms |
| DB commits per refresh | 2 (3.67 ms) | 1 (2.28 ms) |
| SQL statements per refresh | 4 (3.02 ms) | 1 (0.99 ms) |

`blacklist_token()` used to write **two** rows — the raw JWT string and
`jti:{jti}` — each through its own `crud_token_blacklist.create()` call, and
`create()` commits internally. So every refresh paid two remote COMMITs (WAL
fsync on the Postgres VM) plus two post-commit `db.refresh()` SELECTs. Now one
row goes in with a single `db.add()` + `commit()`; the refresh SELECT is skipped
because the inserted row is never read back.

The dropped row was redundancy, not defence in depth: the hot path keys on
`jti:{jti}` whenever a `jti` claim exists, and the DB fallback
(`_check_db_fallback`) probes the `jti:` row first, so it is the row that can
ever match. A jti-less legacy token still gets its raw-token row.

### What is left (measured, 2026-09-22)

Redis is now the largest single resource — 10 commands, 4.55 ms, but spread
thin with no hotspot:

| phase | cost |
|---|---:|
| `blacklist_token` (1 COMMIT + 1 INSERT) | 3.42 ms |
| service-level Redis (lock SET NX, lock DEL, rotation marker SET) | 1.22 ms |
| middleware, outside the service (2 Redis ops) | 1.10 ms |
| `verify_token` (blacklist Lua + token_version GET) | 0.82 ms |
| `register_session` | 0.69 ms |
| `reuse_check` | 0.40 ms |
| `invalidate_session` | 0.39 ms |

Reaching the <10 ms p95 target from here means either pipelining those Redis
ops (~1-1.5 ms recoverable) or taking the durable blacklist write off the
request path (~2.4 ms) — and the latter weakens the fail-closed DB fallback
that covers a Redis outage. Both are owner decisions, not free wins.

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
