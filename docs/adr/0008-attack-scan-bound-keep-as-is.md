# ADR-0008: The attack-detection body scan stays as it is — measured, not assumed

**Status:** Accepted
**Date:** 2026-09-30
**Tracked by bead:** `FastAPI_saas-nrgi.3` — "Measure and bound the attack-detection body scan"

## Context

The bead asked to measure the attack-detection body scan and then bound it,
proposing three options: (a) skip the scan for methods/content types that
cannot carry a payload, (c) move the scan off the event loop via `to_thread`
on a dedicated pool, or none if the scan is cheap. Option (b) — truncating
the scan to the first N bytes — was explicitly forbidden without an explicit
security decision from the threat-model owner. **It was not taken.**

The bead cited the scan at `src/app/middleware/attack_detection.py:223-541`.
Verified: the middleware class opens at :223 and `_scan_request` runs
:381-501; the body-scan block is :428-499. The citation is close enough to be
useful but does not land on the body-scan block itself.

## What the scan actually does

Not "a handful of regexes". Per request it runs one combined 17-group
`re` over **four** inputs: the path, the query string, every security-relevant
header, and — for POST/PUT/PATCH only — the request body. There is no JSON
parse and no entropy analysis. A necessary-literal prefilter
(`_attack_prefilter_hit`, Aho-Corasick) sits in front of the regex: if no
literal is present the regex provably cannot match and is skipped entirely.

## Measurements

Environment: CPython 3.13.7, Windows, `.venv`. Method: the repo's own harness
(`tests/benchmarks/test_r5_micros.py::run_benchmark`/`best_of_benchmark`
helpers), best-of-N over a warm loop, `gc.collect()` between batches.

**Cost of `_scan_request` alone (µs/request):**

| request | cost |
|---|---|
| GET, bodyless | **6.7 µs** |
| POST 77 B JSON (typical login) | **47.8 µs** |
| POST 60 KB clean (prefilter MISS) | 1,087 µs |
| POST 40 KB benign, prefilter **HIT** | 16,015 µs |
| POST 128 KB benign, prefilter **HIT** | 49,621 µs |

**End-to-end request latency (µs, in-process `TestClient`):**

| request | p50 | p90 | p99 |
|---|---|---|---|
| GET bodyless | 3,546 | 5,121 | 6,950 |
| POST 77 B JSON | 4,807 | 6,686 | 8,959 |
| POST 60 KB clean | 9,718 | 12,244 | 15,556 |
| POST 40 KB benign w/ `or` (HIT) | **92,124** | 161,606 | 214,040 |

**Route method mix** (from `app.router.routes`, excluding docs): 94 GET,
64 POST, 27 DELETE, 24 PATCH, 3 PUT — 118 of 212 method slots (55.7%)
carry a body.

## Decision

**Take none of (a), (c), or (b). The bead's two actionable options are
already implemented, and the third is measurably worthless.**

### Option (a) is already implemented — and correctly so

`_scan_request` gates the body scan on `if method in ("POST", "PUT", "PATCH")`
(`attack_detection.py:429`). A bodyless GET never calls `_drain_body` at all —
verified directly: `drain_body` call count on a GET carrying an attack payload
in its body is `0`, and no attack is reported. HEAD, DELETE, OPTIONS and
TRACE are likewise excluded.

The one thing the bead asks for that does *not* exist is the **debug log** on
that skip. Adding it is the single change this bead makes — see below. The
skip itself needs no work.

The gate is also *necessary*, not merely cheap: a GET's attack surface is its
query string and headers, and those **are** still scanned. A GET with
`?q=' OR 1=1` is detected as `sql_or_1_equals_1`. Removing the body scan for
GET does not remove the SQLi coverage of a GET.

### Option (c) does not work — `re` holds the GIL

Decisive A/B, measuring **throughput** of 2,000 trivial coroutines (the only
metric that captures event-loop blocking), while one scan runs:

| body size | inline on loop | offloaded to a thread |
|---|---|---|
| 4 KB | 1.2× | 1.4× |
| 8 KB | 1.3× | 1.6× |
| 32 KB | 3.2× | 2.3× |

Offloading does **not** recover event-loop responsiveness, and at small sizes
it is measurably *worse* than running inline. `re.finditer` on `bytes` does not
release the GIL, so a worker thread still holds the interpreter lock for the
whole match. The existing `_BODY_OFFLOAD_THRESHOLD` dispatch is therefore
best-effort, not a real offload.

The bead's "shared-default-executor concern" was checked and is **not** a
reason to act. Measured against a production-sized 16-thread default executor
(the value asyncio actually provides), an unrelated `asyncio.to_thread` call —
the one bcrypt/S3/stripe/DNS depend on — saw **1.3× p50 inflation** under 8
concurrent scans and only **6 of 16 workers** engaged. A dedicated pool would
have bought isolation from a contention that was not occurring.

### The real cost driver is the prefilter literal, not the scan's existence

`sql_or`'s necessary literal is `b"or"`, a substring of `color`, `for`,
`order`, `orange`, `none`, `word`. **7 of 9 realistic benign JSON bodies
trip the prefilter**, sending them to the full 17-group regex for nothing.
The regex costs ~1 µs/byte on such bodies (a 128 KB benign hit body costs
**190–302 ms** of pure regex).

This is a genuine performance defect — but it is a *detection-tuning* defect,
not the method-gating or event-loop-offload defect the bead describes.
Narrowing `b"or"` to a tighter *necessary* literal is a security-relevant
change to what reaches the regex, and it is therefore deliberately **out of
scope** here: it needs the threat-model owner's sign-off, exactly like
option (b). **Reported, not fixed.**

## The one change made

Add the debug log the acceptance criteria ask for on the method-gated skip —
the only skip path in the scan that is currently silent.

It is guarded by `logger.is_enabled_for(logging.DEBUG)`, and that guard is not
incidental: a bare `logger.debug(...)` through this repo's structlog stack
measures **44.6 µs**, which is **6.7× the cost of the entire GET scan it would
announce**. Unguarded, the "fix" would have made the hot path slower than the
work it reports on. Guarded, the check costs **1.9 µs**.

## Consequences

- The 10 MB body limit and its 413 envelope are unchanged; no test was
  weakened to accommodate this.
- No body is truncated. `_MAX_SCAN_SIZE` (128 KB) and its existing
  skip-and-continue policy are untouched — that policy predates this bead and
  is not re-litigated here.
- The body-scan method gate is now *pinned by tests* rather than incidental, so
  a future edit cannot silently re-introduce GET body draining.
- A malicious payload is verified still detected at 0 %, 50 %, 90 % and 99 %
  depth through a 120 KB body — the assertion that catches truncation.
- Open, for a security decision: the `b"or"` literal, and the 128 KB
  `_MAX_SCAN_SIZE` skip policy that leaves bodies above it unscanned while
  still allowing them through.