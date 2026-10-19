# L-13 (FastAPI_saas-e0ss) — endpoint p95 effect of the body-scan offload

**Recovered 2026-10-19.** This directory holds the artefact the bead closed
with, the raw driver output behind it, and — from 2026-10-19 — the driver
itself, recovered into the repository.

## What the bead was

`FastAPI_saas-e0ss` (L-13) asked for the endpoint-level p95 effect of the
body-scan offload, on a route that actually carries a body. The bead's own
notes forbid `GET /users/me`, because D18 establishes by construction that the
body scan is POST/PUT/PATCH only. The route used was
`POST /api/v1/posts/by-user/{username}`, verified empirically: a plain seeded
`perf_user_*` account gets 201 Created with no permission grant, because the
route is authenticated and self-scoped and `src/app/core/rbac/permissions.py`
has no posts entry.

The result: **NO MEASURABLE p95 EFFECT.** On a body-carrying POST route at the
degradation concurrency, the thread-pool offload moves p95 by **-4.3 %** (60 KB
miss regime) and **+4.5 %** (40 KB hit regime) against a measured intra-arm
noise floor of 15–31 %. Both are indistinguishable from parity. The third arm
the acceptance asks for — a process pool — is **PROVEN IMPOSSIBLE as a
drop-in** by three independent empirical checks.

## The provenance gap, in the artefact's own words

The published report records, of its own instrument:

```json
"instrument": {"file": "/home/ansible/l13_body_offload.py", "in_repo": false}
```

and its first entry in `limits` says:

> The harness lives outside the repo, so this measurement is not reproducible
> from git. The Phase 0 provenance gate would VOID it. Recorded rather than
> committing an ad-hoc driver.

That was honest, and it was also a **citation barrier**: a measurement whose
instrument exists only on one machine's disk cannot be re-checked by anyone
else, so the numbers could be published but not re-derived.

## What changed on 2026-10-19

1. **The driver is now in the repository**: `src/benchmarks/l13_body_offload.py`.
   The measurement code is unchanged; the harness was re-homed, not rewritten.
2. **The raw driver output is now beside the report**: `raw/l13_result.json`
   (7,153 B). This is what the driver wrote, and it is a *different shape*
   from the published report — see "The report is a curation of the raw output"
   below.
3. **An oracle now proves the chain**:
   `tests/unit/benchmarks/test_l12_l13_harnesses.py`.

## What did NOT change

- **The published report's numbers, `limits`, `interpretation`, `headline`,
  `warmth`, `process_arm` and `bead_closes` are untouched.**
- **`"in_repo": false` is left as recorded.** It was true of the run. The fix
  is that the file is now in the repository — recorded in this note and in the
  harness's own docstring — not that the historical field was rewritten. The
  oracle pins that it is *still* `false`.

## The report is a curation of the raw output

| | `raw/l13_result.json` (driver output) | `l13_body_offload.json` (published) |
|---|---|---|
| top-level keys | `bead, blocks, item, knee, knee_note, knee_sweep, logins, process_arm, protocol, ratios, regimes` | `artefact_kind, bead, bead_closes, blocks_at_knee, closed, cross_reference, headline, instrument, interpretation, item, knee_sweep, limits, machine, measured_at, measured_vs_inferred, process_arm, protocol, regimes, title, warmth` |
| per block | `reps, p95_median_of_medians_ms, p95_min_of_medians_ms, p95_max_of_medians_ms, p50_median_of_medians_ms, p50_min_of_medians_ms, per_rep_p95_ms[5], statuses` | the same five statistics, rounded to 3 dp, plus `per_rep_p95_ms` and `statuses` |
| carries a verdict | no | yes |

**The published report is fully derivable from the raw output**, and the oracle
derives it: every published block statistic is the measured statistic, every
knee-sweep row is the measured row, the knee is the measured knee *and* is
re-derived by executing the knee rule (the first concurrency whose p95
exceeds 2.0x the concurrency-1 p95), and the published ratios and noise floor
are the measured ones.

## The verdict is derived, not asserted

L-13's conclusion is **parity**, so the conclusion has to follow from the
figures rather than be claimed beside them. The oracle checks every published
ratio against its own arm's spread: `|ratio - 1.0| * 100 <= spread`. All four
published ratios sit inside the 15–31 % noise floor, so no arm is claimed
faster or slower than the other. The claim is "no effect is **visible at the
stated precision**", not "the effect is exactly zero" — and the report's
`limits` says so in those words.

## The knee, and what its shape means

The knee was located on the inline/hit40k arm and then reused for both
regimes. The sweep's p95 climbs monotonically with concurrency:

| concurrency | 1 | 2 | 4 | 8 | 16 | 32 | 64 |
|---|---|---|---|---|---|---|---|
| p95 (ms) | 43.41 | 85.18 | 154.93 | 306.48 | 836.19 | 1387.28 | 2294.17 |

Absolute p95 keeps climbing roughly linearly rather than plateauing. That is
the signature of a **genuinely serial CPU-bound scan**, not a queue a pool
would relieve — and it is why the offload cannot buy endpoint p95 here.

## The process arm is impossible as a drop-in — three empirical checks

The report records all three, and the oracle re-executes them:

1. **The default executor refuses a process pool.**
   `loop.set_default_executor(ProcessPoolExecutor(...))` raises
   `TypeError: executor must be ThreadPoolExecutor instance` — and production
   calls `run_in_executor(None, ...)`, which is exactly that default executor.
2. **The production callable is not picklable.** The lambda raises
   `AttributeError: Can't get local object 'main.<locals>.<lambda>'`, and
   `self` (an ASGI-app-holding middleware) is not picklable either. The error
   text names the *enclosing function*, which is why the recovered harness
   keeps the lambda inside a function named `main` and not one frame deeper.
3. **The in-place mutation would be silently lost.** `detected_attacks` is
   mutated in place, so a child copy's finds would vanish — a **security
   regression, not a performance question**.

## Provenance facts about the run itself

- **The run tree was not a git repository.** The driver ran against `~/head`
  on the Linux target, a plain directory tree (`fatal: not a git
  repository`). There was no commit to name.
- **The original driver did its environment setup at import time**: `ROOT =
  Path(__file__).resolve().parents[0] / "head"`, `os.chdir(ROOT)`,
  `load_dotenv(ROOT / "src" / ".env", override=True)`, the two
  `os.environ.setdefault` calls, and then the `src.app` imports — all at
  module level. The re-homed harness keeps the same *kinds* of setup
  (chdir, the same dotenv file, the same two setdefaults) but moves them into
  `bootstrap()`, called only from `__main__`, because a module-level
  `load_dotenv(override=True)` in a tracked file would clobber the
  environment a pytest session runs under. **That is a change in where the
  side effects happen, not in what was measured.**
- The harness issues each request from a **fresh loopback source IP** while
  cycling 250 accounts, because the rate limiter keys on IP (5/60) and the
  auto-ban would otherwise trip. An earlier run that drove 100 requests from
  one IP produced 41x403 + 9x429 and was **discarded**.

## What is still not reproducible

Recovering the driver closes the *provenance* gap. It does not make the run
repeatable on demand, and the report's own `limits` still stand:

- app and database share one host, so the latencies exclude real network and
  separate-database cost;
- 2–4 responses per 100 were rejected with 429 and are still inside the
  latency sample, which biases p95 slightly **down** — the parity conclusion
  survives it;
- five reps of 100 samples each is enough to place a 15–31 % noise floor but
  not to resolve a difference smaller than that;
- the knee was located on the inline/hit40k arm only and reused for both
  regimes; the miss regime's true knee could differ;
- the posts route includes a database insert on every request, so this is a
  **lower bound** on what the offload could contribute to absolute p95;
- the warmth is only **PARTIAL**: the thread arm's pool is warm (CPython
  creates the default `ThreadPoolExecutor` lazily and then reuses it), but no
  warm-up rep was discarded, so 1 of the 5 reps is cold. Recorded rather than
  retro-fitted.

## Re-running it

```bash
uv run python src/benchmarks/l13_body_offload.py --output reports/l13_result.json
```

The default output path is `reports/l13_result.json`, and `reports/*.json` is
gitignored (`.gitignore:188`) — so a re-run **cannot** overwrite the tracked
artefact in this directory. It is a fresh measurement, not a re-record. Note
that it drives the ASGI app **in-process** (`httpx.ASGITransport` plus
`asgi_lifespan`), so it needs a reachable database and a seeded tenant, and
`asgi_lifespan` is a **dev** dependency (`pyproject.toml`, `[dependency-groups]
dev`) — it is imported lazily inside the phase that drives the app.

## File inventory

| file | what it is |
|---|---|
| `l13_body_offload.json` | the published report — the bead's record (tracked, unchanged) |
| `raw/l13_result.json` | the driver's raw output for the same run (added 2026-10-19) |
| `FINDINGS.md` | this file (added 2026-10-19) |
| `src/benchmarks/l13_body_offload.py` | the recovered driver (added 2026-10-19) |
| `tests/unit/benchmarks/test_l12_l13_harnesses.py` | the oracle for both L-12 and L-13 (added 2026-10-19) |
