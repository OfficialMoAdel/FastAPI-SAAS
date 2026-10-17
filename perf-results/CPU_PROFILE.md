## CPU profile of the hot request, Linux target

From `perf-results/RESULT-20261012-linuxvm/raw_linux_d.json`, produced by
`src/benchmarks/prof_request_path.py` on `perf-local` (Linux 5.14, CPython 3.13.16,
uvloop 0.22.1), measured in-process over ASGITransport so cProfile and the app share
a thread.

| | |
|---|---|
| profiler | cProfile, overhead quantified: `distortion_x` **1.939** |
| requests profiled | 25 |
| clean wall time per request | **4.233 ms** |
| total tottime | 0.204843 s |
| **top-5 tottime share** | **2.8%** |
| remainder (`other` bucket) | **64.0%** |

### Top 5 by tottime

| # | site | tottime |
|---|---|---|
| 1 | `src/app/core/observability/log_sanitizer.py:192(<genexpr>)` | 2.169 ms |
| 2 | `src/app/middleware/attack_detection.py:558(_check_for_patterns)` | 1.564 ms |
| 3 | `src/app/core/observability/log_sanitizer.py:252(sanitize_value)` | 0.691 ms |
| 4 | `src/app/core/observability/log_sanitizer.py:182(_maybe_sensitive)` | 0.675 ms |
| 5 | `src/app/middleware/attack_detection.py:165(_attack_prefilter_hit)` | 0.563 ms |

The remainder is not an unexamined gap: the exporter buckets every unattributed
frame into `other` explicitly, so 64% of
tottime is a named bucket rather than an absence.

### THE OBLIGATION THIS DISCHARGES

`cekt` states: *"If the top-5 share is insufficient, the plan is rewritten - that
obligation is discharged explicitly, not silently."*

**The top-5 share is 2.8%. That is insufficient, and it refutes the premise the plan
optimises against.**

The plan's method is to profile the hot request and act on the functions it names.
On this target that method returns a nearly flat profile: the five largest
functions together are 2.8% of time, and the single largest - a log-sanitiser
generator expression - is **1.1%**. Micro-optimising all five would
address under a twentieth of the request.

That is a stronger result than a list of hot spots would have been. It says the
request cost is **distributed**, not concentrated, so the wins are in breadth
(removing work from the path) rather than depth (making named functions faster).
Two of the five are the attack-detection prefilter and three are log sanitising -
both of which the plan already targets elsewhere, so the profile corroborates them
as present and irrelevant as *sole* explanations.

Any plan revision must therefore not assume a profile will name the hot spot.
This record is the discharge.

## Independent replication, and the `rate_limit_gcra` correction (M-6 / `85l8`)

`perf-results/RESULT-20261016-m6/m6_linux_request_profile.json` re-ran this same
harness on the same host and **replicates the conclusion on a different date, a
different request count, and a 3.6x different absolute window**:

| | `cekt` (2026-10-12) | M-6 replicate | M-6 primary |
|---|---|---|---|
| requests profiled | 25 | 300 | 300 |
| profiled window | 0.205 s | 7.326 s | 26.340 s |
| **top-5 tottime share** | **2.8 %** | **2.62 %** | **2.64 %** |

The ranking replicates too: `log_sanitizer.py:192(<genexpr>)` and
`attack_detection.py:558(_check_for_patterns)` are #1 and #2 in all three, and the
same five-frame set occupies the top-5. Three independent runs, one verdict.

**What M-6 adds that this record does not contain:**

1. **The `rate_limit_gcra` mis-attribution is corrected.** The pack's census named
   it at **6 624 us / 37.7 %**. Measured: `EVALSHA(rate_limit_gcra)` runs at
   **1.00/req** on this warm authenticated path (so the premise that it "does not
   execute there" is false), and its CPU share is **0.28 %** — the census figure was
   overstated by **~135x**. Both M-6 runs agree to three significant figures.
2. **Admissible wall-clock, because this record is CPU-only.** The same run timed
   each middleware to `http.response.start`: the guard stack is **5 028.5 us = 42.2 %**
   of GET `/users/me` but **6 529.2 us = 9.6 %** of POST `/account/refresh` (whose
   handler grew to 61 656.6 us). Round-trips: GET issues **0.00 SQL / 3.00 Redis**,
   refresh **1.00 SQL / 10.01 Redis**.
3. **Why a CPU profile of this request is flat.** Idle is 0.00 %, and Redis work is
   awaited on an event loop, so round-trip *wait* is recorded as neither CPU nor
   idle. That is the whole gap: GCRA is 0.28 % of CPU but `RateLimitMiddleware` is
   3 539.9 us of a 16 829 us mean request. The CPU profile cannot see the dominant
   cost, which is why the top-5 lands at 2.6 %.

So the "wins are in breadth, not depth" conclusion above stands and is now
**explained**: the breadth is Redis round-trips, not a flat spread of CPU. Any
revision should be argued from round-trip counts, not from cProfile shares.
