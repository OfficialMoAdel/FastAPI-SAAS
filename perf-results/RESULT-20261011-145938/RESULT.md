# Perf run 20261011-145938 — two-host measurement, DIAGNOSTIC ONLY

**Status: VOID under unified plan §4.5.** 7 failures in 204 requests.
**The numbers below are real and reproducible. They are NOT a certified baseline
and must not be quoted as one.** This document exists so that distinction travels
with the figures.

---

## 1. Why this run is void, precisely

Unified plan §4.5: *a phase returning 429/401 **is void**.* Seven requests did.

| Endpoint | Reqs | Failures |
|---|---|---|
| `POST /account/login (setup)` | 10 | 0 |
| `POST /account/logout` | 1 | 0 |
| `POST /account/refresh` | 7 | **4** |
| `GET /users/me` | 32 | **3** |
| `GET /users/me (readonly)` | 154 | 0 |
| **Aggregated** | **204** | **7** |

Every failure is `HTTP 401` on a path that needs a live token. None is a
server-side fault: the app returned 4xx because the caller's session had been
revoked, not because the endpoint misbehaved.

### The cause is a design decision, not a defect

**Logout is a weighted task — 10% of measured traffic** (`tests/performance/locustfile.py`,
mix 80/10/10/10). Logout destroys the session server-side, so the refresh token
is revoked along with the access token. Any request in flight or queued behind it
therefore carries credentials that are already dead.

Someone moved logout there deliberately: at `on_stop` every logout sample was a
shutdown-burst sample taken while the event loop and both pools were closing, so
its numbers described teardown contention rather than the handler. **The trade is
real and both sides of it are defensible.** What was not previously true is that
the choice *voids the phase*, which is worth surfacing rather than absorbing.

### Three ways out — none taken unilaterally

1. **Remove logout from the measured mix.** Nothing revokes a live session, so no
   401s. Loses logout latency data entirely.
2. **Keep logout, accept the void, label the artefact.** ← *this document*
3. **Return logout to `on_stop`.** Its samples become shutdown-burst and, by the
   file's own account, uninterpretable.

Option 2 is the honest intermediate: the figures describe a real system under a
real load, and the reason they are not certifiable is written down.

---

## 2. Measurements (real, but uncertified)

204 requests · 10 virtual users · 30 s cold window · ~0.55 ms LAN RTT.

| Endpoint | Reqs | p50 | min | max | req/s |
|---|---|---|---|---|---|
| `GET /users/me` (readonly) | 154 | **6 ms** | 4.5 ms | 16.2 ms | 5.33 |
| `GET /users/me` | 32 | **5 ms** | 4.5 ms | 14.5 ms | 1.11 |
| `POST /account/refresh` | 7 | 8 ms | 3.1 ms | 14.7 ms | 0.24 |
| `POST /account/logout` | 1 | 11.4 ms | 11.4 ms | 11.4 ms | 0.03 |
| `POST /account/login` (setup) | 10 | **300 ms** | 300 ms | 343 ms | 0.35 |
| **Aggregated** | **204** | **6 ms** | 3.1 ms | 343 ms | 7.06 |

`GET /users/me` **p50 of 5–6 ms** sits inside the Round-1 pack's 4–9 ms range —
now measured on Linux/uvloop with production budgets, which no earlier baseline
was. `POST /account/login` at 300 ms is bcrypt at `rounds=12`; the addendum's
F-29 had discarded 902 µs as ~2 orders of magnitude too low.

---

## 3. Provenance (complete and verifiable)

| | |
|---|---|
| App host | `perf-app` 192.168.121.4 — Debian 12, AMD EPYC 7763, 2 cores |
| Load host | `perf-load` 192.168.121.5 |
| LAN RTT | 0.55 ms ping / 8 ms over HTTP |
| Event loop | **uvloop** — confirmed mapped in `/proc/1/maps` of the running server |
| HTTP | **httptools** — confirmed mapped in the running server |
| Python / uvicorn | 3.13.16 / 0.34.3, `--loop auto --http auto` |
| Image digest | `sha256:39a80dbe9a9a9a987173a8b40645e7372d6bf73c6f7cb78d47bedb7fd6fd2b09` |
| Image source | pushed to a **local registry** to obtain a genuine OCI digest |
| Store locality | `co-located` — db + redis containers on the app host |
| Budgets | `auth (5,60)` · `refresh (60,60)` · anonymous `(10,3600)` · auto-ban **on** |

### Recorded deviations (§4.2 requires the exact value in the artefact)

| Deviation | Value | Why |
|---|---|---|
| `ip_whitelist_bypass_rate_limit` | `true` for 192.168.121.5 | load generator is one host; every other caller unaffected |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 30 → 120 | **turned out NOT to be the cause** — a 2 min run cannot outlive a 30 min token. Kept so it is demonstrably excluded rather than assumed. *Candidate for reversion.* |
| `AUTH_MAX_SESSIONS` | 5 → 50 (column max) | each phase re-authenticates; oldest-session eviction invalidates the prior token |

---

## 4. Defects found and fixed to reach a measurable run

| Defect | Effect |
|---|---|
| **Seeder/locustfile username mismatch** | harness could not authenticate its own accounts — `username=perf_user_N` stored, `username=<email>` sent, and `varchar(20)` makes the email impossible. 94 requests, all failing. One `perf_username()` now serves both sides, +14 regression tests |
| **Tiers never created** | `create_tables()` makes the table, not the row → FK violation. Also: an un-tiered account falls to the anonymous 10/hr budget, so un-tiered runs void on 429s instead of measuring |
| **`scripts/` absent from the image** | the image could not run the measurement it was built for |
| **Refresh cannot recover from logout** | the only recovery path was the call that cannot work; run voided itself |
| **Doomed refresh attempted** | recorded a 401 whose outcome was decided before the call |
| `.gitignore` `perf_*` | silently untracked `src/benchmarks/perf_accounts.py` — a source module |

Error count across these fixes: **92 → 29 → 13 → 8** (7 in the cold window).

Also found, pre-existing and unrelated to two-host work: `docker-compose.yml`'s
`environment:` keys parsed as `null` (Compose rejected the file repo-wide);
`.dockerignore`'s `*.md` hid the `README.md` that `uv sync` requires, so **no
image had ever built**; the Dockerfile never created `/code/logs`, so the
non-root user could not write logs.

---

## 5. What a certified baseline still needs

Not a gap in the plumbing — the plumbing works. Missing:

- **The logout decision (option 1, 2 or 3 above)** made explicitly.
- **Gate A sample sizes.** 30 s with 10 users is a smoke test. p99 needs
  n ≥ 10 000; a real arm needs the ≥30 min RSS window, the concurrency sweep,
  and ≥5 repetitions.
- **Two distinct defects closed upstream** — none of the numbers here may be
  compared against the Round-1 Windows pack, which A-5 marks non-representative.

---

## 6. Files

`baseline_20261011_145938_manifest.json` — provenance, run params, cleanup, hold
`app_host_report.json` — the app host's own view (uvloop, httptools, budgets)
`baseline_20261011_145938_cold_stats.csv` — per-endpoint aggregate
`baseline_20261011_145938_cold_stats_history.csv` — per-interval, locust's record
`baseline_20261011_145938_cold_failures.csv` / `_exceptions.csv` — the void, itemised
`baseline_20261011_145938_cleanup.json` / `_actual.json` — planned vs actual teardown
`locust_baseline_20261011_145938.log` — full locust output
`run.log` — harness console

Copies: `~/RESULT-20261011-145938/` on perf-load ·
`D:\project\FastAPI_saas\perf-results\RESULT-20261011-145938\` locally.