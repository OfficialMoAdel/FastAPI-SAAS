# Perf run 20261011-124314 — two-host baseline

**Status:** measurements captured, run **VOID** (exit 1) — 10 auth errors, see §4.

## 1. Topology and provenance (the part that was hard to get)

| | |
|---|---|
| App host | `perf-app` 192.168.121.4, Debian 12 (bookworm), AMD EPYC 7763, 2 cores |
| Load host | `perf-load` 192.168.121.5 |
| LAN RTT between hosts | **0.55 ms** (ping), 8 ms measured over HTTP |
| Event loop | **uvloop** — confirmed mapped in `/proc/1/maps` of the running server |
| HTTP impl | **httptools** — confirmed mapped in the running server |
| Python | 3.13.16, uvicorn 0.34.3, `--loop auto --http auto` |
| Image digest | `sha256:39a80dbe9a9a9a987173a8b40645e7372d6bf73c6f7cb78d47bedb7fd6fd2b09` |
| Image source | pushed to a **local registry** (`registry:2` on perf-app) to obtain a genuine OCI digest |
| Store locality | `co-located` — db + redis are containers on the app host |

Full record: `baseline_20261011_124314_manifest.json`, `app_host_report.json`.

## 2. Security budgets — production values, in force

| Budget | Value |
|---|---|
| `auth` | (5, 60) |
| `refresh` | (60, 60) |
| `mfa_verify` | (5, 60) |
| `default_anonymous` | (10, 3600) |
| `auto_ban_enabled` | true |

**Deviation, recorded as §4.2 requires:** `ip_whitelist_bypass_rate_limit: true` for
192.168.121.5. The load generator is one host whose IP bypasses the limits; every
other caller is unaffected. The value is in `app_host_report.json` → `limits`, and
the harness carried it into the manifest verbatim.

## 3. Measurements

210 requests, 10 users, 30 s cold window.

| Endpoint | Reqs | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| `GET /users/me` | 36 | **6 ms** | 15 ms | 18 ms | 18 ms |
| `GET /users/me` (readonly) | 153 | **6 ms** | 10 ms | 15 ms | 19 ms |
| `POST /account/refresh` | 10 | 15 ms | 67 ms | 67 ms | 67 ms |
| `POST /account/login` | 10 | **310 ms** | 330 ms | 330 ms | 330 ms |

`GET /users/me` p50 of 6 ms sits inside the Round-1 pack's 4–9 ms range — now
measured on Linux/uvloop with production budgets, which no earlier baseline was.

`POST /account/login` at 310 ms is bcrypt at `rounds=12`. The addendum (F-29) had
discarded a 902 µs figure as ~2 orders of magnitude too low; this confirms that
direction from the other side.

## 4. Why the run is VOID

Exit 1. Ten errors, all auth:

```
5  GET  /users/me        Token expired
5  POST /account/refresh Refresh failed: HTTP 401
```

Unified plan §4.5 makes a 401 a void condition. **The harness is correct to
refuse** — the numbers above are real but not certifiable.

Most likely cause: access-token TTL shorter than the run window, so tokens minted
early in the cold window expire before the hold completes.

## 5. Defects found — fix before the next run

Ordered by what blocks the next run first.

### 5.1 Redis had no password (was a 500 on every login)
The perf stack ran `redis:7` with no `--requirepass`, but the app sends `AUTH`
and the real deployment at 192.168.121.147 *does* use a password. Every login
died with `AuthenticationError` → HTTP 500.
**Fix:** `redis:7` with `command: ['redis-server','--requirepass','<pw>']`, and
`REDIS_CACHE_PASSWORD` / `REDIS_QUEUE_PASSWORD` / `REDIS_RATE_LIMIT_PASSWORD` /
`CRUD_ADMIN_REDIS_PASSWORD` set on `web`.

### 5.2 The load host was auto-banned
Repeated manual probing crossed 10 violations → `security:ban:192.168.121.5` →
every subsequent request answered `403 Request from banned IP` from the
auto-ban path, not the auth path.
**Fix:** clear ban state before a run:
```
redis-cli -a <pw> --scan --pattern 'security:ban*' | xargs -r redis-cli -a <pw> DEL
redis-cli -a <pw> --scan --pattern 'security:ban_count*' | xargs -r redis-cli -a <pw> DEL
redis-cli -a <pw> --scan --pattern 'gcra:*' | xargs -r redis-cli -a <pw> DEL
```

### 5.3 Seeder/locustfile username mismatch (a real harness bug)
`scripts/setup_locust_test_data.py` creates `username = perf_user_<N>`, but
`tests/performance/locustfile.py` posts `{"username": self.email}`. The account is
looked up by the `username` field, so an account whose username is not its email
can never authenticate. `users.username` is `varchar(20)`, so a normal
`perf_user_0@example.com` (23 chars) cannot be used as a username at all —
**this makes the harness unable to authenticate its own seeded accounts**, not
just awkward.
**Worked around here** with short identifiers (`pu0@e.io`) and
`UPDATE users SET username = email`.
**Proper fix:** either the seeder sets `username = email` with the column
widened, or the locustfile sends the account's `username`.

### 5.4 Tokens expire mid-run (the void cause)
See §4. **Fix:** shorten the run below the access-token TTL, or raise
`ACCESS_TOKEN_EXPIRE_MINUTES` for the perf stack only — and record it, as with
the whitelist bypass.

### 5.5 Seeder prerequisites not created by `create_tables()`
`setup_locust_test_data.py` needs a `tier` row (`PERF_TEST_TIER_ID = 2`);
`create_tables()` creates the table but not the row →
`ForeignKeyViolationError: users_tier_id_fkey`.
**Fix:** seed `tier` rows (id 1 free, id 2 premium) before seeding users.

### 5.6 `scripts/` is excluded from the image
`.dockerignore` lists `scripts`, so `setup_locust_test_data.py` is not in the
container and cannot be run there without a `docker cp` and a reconstructed
`src/` layout.
**Fix:** run the seeder from a host-side venv pointed at the compose network, or
add a `perf` stage that copies the scripts in.

## 6. Docker findings worth recording in the repo

These are pre-existing and unrelated to the two-host work — the Docker path has
never been runnable as committed.

| Defect | Symptom |
|---|---|
| `docker-compose.yml` has `environment:` followed only by comments | parses as `null`; Compose rejects it — `docker compose` was broken repo-wide |
| `.dockerignore` has `*.md` | hides `README.md`, which `pyproject.toml` declares as `readme`; `uv sync` fails, so **no image could ever build** |
| `Dockerfile` never creates `/code/logs` | non-root `app` cannot write logs → `PermissionError` at startup |
| `docker-compose.yml` `web` mounts `./src/app:/code/app` | image puts code at `/code/src`; the mount is vestigial and misleading |
| `build: null` does **not** clear an inherited `build:` in Compose v5.6.0 | merging an override that adds `image:` yields "build tag cannot contain a digest" |

## 7. Files

| File | What it is |
|---|---|
| `baseline_20261011_124314_manifest.json` | the manifest — provenance, run params, cleanup, hold |
| `app_host_report.json` | the app host's own view: uvloop, httptools, budgets, deployment |
| `baseline_20261011_124314_cold_stats.csv` | per-endpoint aggregate |
| `baseline_20261011_124314_cold_stats_history.csv` | per-interval, locust's own record |
| `baseline_20261011_124314_cold_failures.csv` / `_exceptions.csv` | the void condition, itemised |
| `baseline_20261011_124314_cleanup.json` / `_actual.json` | planned vs actual teardown |
| `locust_baseline_20261011_124314.log` | full locust output |
| `run.log` | harness console |

Both copies exist: `~/RESULT-20261011-124314/` on perf-load, and
`D:\project\FastAPI_saas\perf-results\RESULT-20261011-124314\` locally.