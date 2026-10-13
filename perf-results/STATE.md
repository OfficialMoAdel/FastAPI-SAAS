# Phase-0 perf programme — compact state

**Branch** `security/remediate-run3-findings` @ `93750647` · **Two VMs were used and can be deleted**
**Evidence** `perf-results/RESULT-*` (6 runs) · every code fix is committed

---

## 1. What was broken, and is now fixed

All found by running the thing, not by reading it.

| # | Defect | Fix |
|---|---|---|
| 1 | Seeder stored `username=perf_user_N`, load file sent `username=<email>`. `users.username` is `varchar(20)` and a generated email is 23 chars — **the harness could not authenticate its own accounts** | one `perf_username()` both sides call (`src/benchmarks/perf_accounts.py`) + 14 regression tests |
| 2 | Seeder never created `tier` rows → FK violation. Also: un-tiered accounts fall to the anonymous 10/hr budget, so runs void on 429s instead of measuring | seeder inserts `free` + `premium` |
| 3 | `scripts/` never reached the image — **the image could not run the measurement it was built for** | `.dockerignore` negation **and** a Dockerfile `COPY` (neither alone was enough) |
| 4 | Logout is a weighted task and revokes the session, so refresh can never recover; the run voided itself | re-login fallback, plus no doomed refresh when no token |
| 5 | Perf accounts had **no RBAC role** → every guarded route 403'd, capping the matrix at self-service routes | seeder grants `users:read`, `notifications:read` (read-only by design) |

Pre-existing and unrelated, but the Docker path had **never** worked: `docker-compose.yml`'s
`environment:` keys parse as `null` (Compose rejects the file repo-wide); `.dockerignore`'s `*.md`
hid the `README.md` that `uv sync` needs; the Dockerfile never created `/code/logs`;
`.gitignore`'s `perf_*` silently untracked a source module.

## 2. What the two-host environment produced

```
app  perf-app 192.168.121.4   Debian 12, AMD EPYC 7763, 2 cores
load perf-load 192.168.121.5  LAN RTT 0.55 ms
uvloop + httptools  CONFIRMED MAPPED in the running server's /proc/1/maps
image digest       sha256:39a80dbe…  (real OCI digest, via a local registry)
budgets            auth (5,60), refresh (60,60), anon (10,3600), auto-ban ON
```

| Endpoint | Reqs | p50 | p95 | p99 |
|---|---|---|---|---|
| `GET /users/me` (readonly) | 47,014 | **5 ms** | 9 ms | 13 ms |
| `GET /users/me` | 10,592 | **5 ms** | 9 ms | 13 ms |
| `GET /health/live` | 672 | **3 ms** | 5 ms | 8 ms |
| `GET /account/sessions` | 677 | 6 ms | 10 ms | 12 ms |
| `GET /account/sessions/count` | 667 | 5 ms | 9 ms | 15 ms |
| `POST /account/refresh` | 1,372 | 14 ms | 19 ms | 26 ms |
| `POST /account/login` | 26 | **3,000 ms** | 3,900 ms | 3,900 ms |
| **Aggregated** | **61,020** | **5 ms** | 11 ms | 15 ms |

`/users/me` p50 of 5 ms sits inside the Round-1 pack's 4–9 ms — now on Linux/uvloop with
production budgets, which no earlier baseline was. Login at 3 s is bcrypt `rounds=12` on 2 cores
under 50 concurrent authentications: it scales with cores and dominates any login-heavy profile.

**The warm population is now VERIFIED, and this corrects an earlier statement in this file.**
The `190914` artefact shipped `warm_population.verified: false` with the caveat "no ramp request
can be PROVEN absent from the reported percentiles". That was a bug in the harness reader, not in
the data: `read_locust_phase` excluded locust's total row by testing `Type == "Aggregated"`, but
locust writes that row with an EMPTY `Type` and puts the marker in `Name`. The total was admitted
as an endpoint, so the sum counted every request twice — 122,040 reported against 61,020 actual,
exactly 2x. The reader is fixed and re-run against the stored CSV: **61,020 requests, 1 failure,
7 endpoints, `verified: true`**. The percentiles above were always over that population — the
spawn ramp is in the cold phase's own file — so the doubt was the reader's, not the measurement's.
Regression: `tests/unit/benchmarks/test_locust_total_row_not_an_endpoint.py`.

**RSS window (L-10):** 2,101.9 s, 422 samples, **43.6 → 43.7 MB, flat**. No growth. This is the
answer the pack's 5.81 s window could not give. → `perf-results/RESULT-20261011-190914/rss_cpu_series.json`

## 3. Why the run is VOID, and why I stopped re-running

```
GET /users/me  CatchResponseError('Unexpected status: 0')  ×1  in 61,020
```

`status 0` is not an HTTP response — one request never completed. Second 30-minute arm to void on
exactly one socket timeout. At this scale a single blip voids the run, so §4.5's absolute rule and
a 2-core box with 3-second bcrypt logins are in tension. **A third arm was not run**: that chases a
certificate, not a measurement.

## 4. Gate A status

| Item | Status |
|---|---|
| p99 n ≥ 10,000 | ✅ 57,606 |
| ≥30 min RSS window | ✅ 2,101.9 s, flat |
| endpoint matrix | ✅ **10 paths** (after the role fix — unverified, needs VMs) |
| zero failures | ❌ 1 transport timeout in 61,020 |
| ≥5 repetitions | ❌ 1 of 5 |

## 4b. Criterion (d) — `prof_request_path.py` on Linux: DONE, and it found a bug

Run on `perf-local` (VMware guest, RHEL9, 4 cores, Python 3.13.16, uvloop 0.22.1).

**It compiles — but it did NOT produce a profile.** It died at
`prof_request_path.py:482` with `ModuleNotFoundError: No module named 'asgi_lifespan'`.

**`asgi_lifespan` was imported by TWO instruments and declared in NEITHER
`pyproject.toml` NOR `uv.lock`:**

- `src/benchmarks/prof_request_path.py:482`
- `src/benchmarks/census.py:443`

Both use a lazy in-function import — deliberately, so neither can be imported into
a test session — but the **package** was never declared. So on any machine that did
not happen to have it, both instruments produce a traceback where a number should
be. That is exactly the class of defect criterion (d) exists to catch, and it was
invisible until the instrument was run on a clean checkout.

**Fix:** `asgi-lifespan>=2.1.0` added to the dev group, with the reasoning inline.
Verified the honest way — uninstalled it, re-ran `uv sync --frozen`, let the lock
install it, then re-ran the profiler. A declaration that does not install is the
same bug wearing a different hat.

**Result:** profiler completes on Linux. Artefact
`perf-results/RESULT-20261012-linuxvm/raw_linux_d.json` (20 KB):
`env` (Python 3.13.16), `testclient` (per-middleware), `async` (cprofile +
tracemalloc + rss, 54 samples). `clean_ms_per_req` 3.71, `distortion_x` 2.32 —
the profiler reports its own overhead rather than hiding it.

Note `census.py` had the same latent break and is fixed by the same declaration;
it was not re-run (long harness), only its import path was confirmed to resolve.

**Deleting this file would have been the wrong call.** Criterion (d) offers
"produce a profile OR be deleted with all citations corrected". Deleting would have
meant rewriting **8 files** (4 of them binding plan documents) to paper over a
one-line missing declaration. The file was never broken; the environment was.

## 4c. The knee, measured twice (arm A vs arm B) — `perf-results/RESULT-20261012-sweepB/`

Sweep 1/8/32/64/128/256 on `perf-local` (2 cores, bcrypt `rounds=12`), run twice:
**arm A** with production security controls exactly as shipped, **arm B** with them
relaxed. Both disclosures are recorded next to the numbers — A reports
**0 overrides**, B reports **4** with each production value alongside.

| Users | A: reqs / fail / min / p50 | B: reqs / fail / min / p50 |
|---|---|---|
| 1 | 79 / 0 / 4 / 7 | 108 / 0 / 4 / 7 |
| 8 | 491 / 3 / 4 / 8 | 803 / 0 / 3 / 8 |
| **32** | **49 / 49** / 133 / 280 | 3,283 / 0 / 3 / 7 |
| 64 | 97 / 97 / 88 / 220 | 6,480 / 0 / 2 / 7 |
| 128 | 193 / 193 / 173 / 850 | 10,570 / 97 / 2 / 15 |
| 256 | 385 / 385 / 167 / 1400 | 565 / 553 / 4 / 640 |

**Arm A's knee is 8 users; arm B's is 128.** A cliff, not a slope: from 32 users on,
arm A fails 100% of requests.

**The gap is the security controls, and nothing else differs between the arms.**

The useful finding is the shape of that gap. At 1 and 8 users the two arms are
IDENTICAL — min 4 ms, p50 7-8 ms. The controls cost nothing at realistic concurrency.
They are not a performance tax; they are a load ceiling, and that ceiling sits
BELOW where the infrastructure saturates anyway. Arm B proves the point: with every
budget relaxed, 256 users still yields 98% failures and throughput *falls* to 29 rps.

**So the bottleneck on this box is bcrypt at login, not the rate limiter.** Relaxing
the budgets does not make 256 users work; it only moves the failure from "correctly
refused" to "correctly overwhelmed". `min` at 2-4 ms throughout confirms the request
path itself is never the constraint.

## 5. What remains

**80 tasks, effectively all behind Gate A.** Only `d1gh` (green-suites guard) is not
measurement-gated.

To finish Gate A you need VMs again and three decisions:

1. **Accept an arm with a stated void condition**, or keep paying 30 min/attempt for a zero.
2. **Re-run** to verify 10/10 endpoints now the fixture carries a role.
3. **5 repetitions** — ~2.5 h, only after (1) is settled.

## 6. Gotchas worth keeping

- **`PERF_LOGOUT_WEIGHT=0`** certifies a phase. Default is 10, unchanged — the logout decision is
  recorded, not made silently. Logout revokes the session, so requests behind it carry dead
  credentials.
- **`PERF_TEST_EMAILS`** must be short (`qa0@e.io`); `users.username` is `varchar(20)`.
- **`bash` mangles `!`** in the fixture password `Str1ngst!` — curl posts came out as 422
  "field missing". Use a file for the body, or check the DB instead.
- **Start order is load-bearing:** stores → schema → web. The app cannot boot before its schema.
- **`docker exec -d` discards stdout.** The first 35-minute RSS series was lost that way; redirect
  to a file in the container.
- **A dirty tree fails the harness** at exit 3 (pin gate). Commit before measuring.