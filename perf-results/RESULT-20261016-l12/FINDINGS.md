# L-12 (FastAPI_saas-liy7) — pool throughput ratios for the body-scan offload

**Recovered 2026-10-19.** This directory holds the artefact the bead closed
with, the raw driver output behind it, and — from 2026-10-19 — the driver
itself, recovered into the repository.

## What the bead was

`FastAPI_saas-liy7` (L-12) asked for the throughput ratio of the body-scan
offload at the production core count, on the Linux target, with warm pools
only. The unit of work is the exact call the middleware makes:
`AttackDetectionMiddleware._check_for_patterns(payload, "body", attacks)`.

The result: a **thread pool buys almost nothing** (1.33x on the prefilter-skip
regime, indistinguishable from parity on the full-regex regime) while a
**process pool buys 2.08x and 2.61x**. The two regimes disagreeing about the
thread arm is itself the finding.

## The provenance gap, in the artefact's own words

The published report records, of its own instrument:

```json
"instrument": {"file": "/home/ansible/l12_pool_ratios.py", "in_repo": false}
```

and its first entry in `limits` says:

> The driver lives outside the repo at /home/ansible/l12_pool_ratios.py, so this
> measurement is not reproducible from git and would fail the Phase 0 provenance
> gate. Recorded rather than committing an ad-hoc harness.

That was honest, and it was also a **citation barrier**: a measurement whose
instrument exists only on one machine's disk cannot be re-checked by anyone
else, so the numbers could be published but not re-derived.

## What changed on 2026-10-19

1. **The driver is now in the repository**: `src/benchmarks/l12_pool_ratios.py`.
   The measurement code is unchanged; the harness was re-homed, not rewritten.
2. **The raw driver output is now beside the report**: `raw/l12_result.json`
   (16,468 B). This is what the driver wrote, and it is a *different shape*
   from the published report — see "The report is a curation of the raw output"
   below.
3. **An oracle now proves the chain**:
   `tests/unit/benchmarks/test_l12_l13_harnesses.py`.

## What did NOT change

- **The published report's numbers, `limits`, `interpretation`, `headline`,
  `noise_floor` and `bead_closes` are untouched.** They were the record of
  the run; correcting a record to match the present is how a stored result
  stops being a record.
- **`"in_repo": false` is left as recorded.** It was true of the run: the
  report was produced by a file that was on one machine's disk and nowhere
  else. The oracle pins that it is *still* `false` — the fix is that the file
  is now in the repository, and this note and the harness's docstring say so,
  not that the historical field was rewritten.

## The report is a curation of the raw output

The two files are not the same document, and it is worth being explicit about
the relationship:

| | `raw/l12_result.json` (driver output) | `l12_pool_ratios.json` (published) |
|---|---|---|
| top-level keys | `schema, item, bead, machine, protocol, windows_reference_not_used_as_before_arm, regimes` | `artefact_kind, bead, bead_closes, closed, cross_reference, headline, instrument, interpretation, item, limits, machine, measured_at, measured_vs_inferred, noise_floor, prefilter_properties_asserted_before_timing, protocol, regimes, relation_to_the_prior_shape, schema, title, windows_reference_not_used_as_before_arm` |
| per arm | `rep_medians_scans_per_sec[5]`, `median_of_medians_scans_per_sec`, `min_of_medians_scans_per_sec`, `max_of_medians_scans_per_sec`, `all_rep_medians_equal`, `arm`, `pool_size`, `concurrency`, `tasks_submitted_per_repetition`, `scans_per_task`, `iterations_per_repetition`, `repetitions`, ratio keys | `scans_per_sec`, `min_of_medians`, `ratio_median`, `ratio_min` |
| carries a verdict | no | yes |

**The published report is fully derivable from the raw output**, and the
oracle derives it: every published arm figure is the measured median, every
published ratio is the measured ratio, the headline is the
`at_production_core_count` block, and each hand-written noise-floor line
quotes the arm it describes. The one deliberate difference is that the
report's `process_over_thread` is the ratio of the two **published** (2 dp)
figures, which is why it is not the exact inverse of the raw 3 dp
`thread_over_process` (0.328 vs 1/3.044 = 0.32851).

## The verdict is derived, not asserted

The report's three-way claim is reproduced from the published figures by
overlap rather than by opinion: an arm's advantage is a result only when the
two arms' measured ranges do not overlap, so the ratio's plausible interval
is `[arm.min / other.median, arm.median / other.min]`, and parity is excluded
exactly when that interval does not contain 1.0.

- **process arm, both regimes**: the interval lies above 1.0 — the advantage
  is established.
- **thread arm, miss regime**: the interval lies above 1.0 — the advantage is
  established (modest).
- **thread arm, hit regime**: the interval **contains** 1.0 — parity is *not*
  excluded, which is what the report's "no reliable gain" means. It is
  explicitly *not* called a regression.

## The bug the artefact's own `instrument` block calls out

> The first version of this harness submitted ONE task per repetition, and
> that task looped over all iterations internally. A pool of 4 therefore
> received exactly one task and three workers sat idle. … The first (wrong)
> run reported thread@4 at 0.701 on the miss regime; the corrected run
> reports 1.334.

This is the property most likely to rot silently in a re-homed harness: a
regression to one-task-per-repetition would still publish internally
consistent numbers, and the only symptom would be "pool size has no effect".
It is now pinned twice — per arm in the recorded raw output
(`scans_per_task == 1`, `tasks_submitted_per_repetition == 12`,
`concurrency == pool_size`) and in the harness's own source (every
`executor.submit` in `measure` asks for exactly one iteration).

## Provenance facts about the run itself

These are properties of the 2026-10-16 run, recorded here because the report
could not record them:

- **The run tree was not a git repository.** The driver ran against
  `~/head` on the Linux target, a plain directory tree (`fatal: not a git
  repository`). There was no commit to name.
- **The original driver took its environment from the SHELL.** It did
  `ROOT = Path(__file__).resolve().parents[0] / "head"`, `os.chdir(ROOT)` at
  module level, imported `src.app.middleware.attack_detection` at module
  level, and never called `load_dotenv`. The re-homed harness calls
  `bootstrap()`, which chdirs to the repository root and loads
  `src/.env`. **That is a change in where the environment comes from, not a
  change in what was measured** — the measurement needs only a configured
  environment, and both sources provided one.
- The original also did its import-time side effects at import time, which is
  why the re-homed harness defers every `src.app` import into a function.
  The house rule is in `src/AGENTS.md`: an operator harness holds its side
  effects in a `bootstrap()` called only from `__main__`.

## What is still not reproducible

Recovering the driver closes the *provenance* gap. It does not make the run
repeatable on demand, and the report's own `limits` still stand:

- it is a microbenchmark, not a request path — no ASGI, no middleware stack,
  no tenant resolution, no database;
- single host, single run, 60 scans per arm — enough to separate 2x from 1x,
  not enough to separate 1.33x from 1.0x on the miss regime;
- `fork` flatters the process arm (children inherit the compiled regex and
  automaton); the gap to a spawn-based deployment is not measured;
- the payload is pickled for the process arm and passed by reference to the
  others — that asymmetry is left in the ratio on purpose, so the process
  figure is a **floor**, not a ceiling;
- only two body regimes were measured (60 KB and 40 KB), chosen to match
  `scripts/body_scan_regimes.py` rather than to span the space.

## Re-running it

```bash
uv run python src/benchmarks/l12_pool_ratios.py --output reports/l12_result.json
```

The default output path is `reports/l12_result.json`, and `reports/*.json` is
gitignored (`.gitignore:188`) — so a re-run **cannot** overwrite the tracked
artefact in this directory. It is a fresh measurement, not a re-record.

## File inventory

| file | what it is |
|---|---|
| `l12_pool_ratios.json` | the published report — the bead's record (tracked, unchanged) |
| `raw/l12_result.json` | the driver's raw output for the same run (added 2026-10-19) |
| `FINDINGS.md` | this file (added 2026-10-19) |
| `src/benchmarks/l12_pool_ratios.py` | the recovered driver (added 2026-10-19) |
| `tests/unit/benchmarks/test_l12_l13_harnesses.py` | the oracle for both L-12 and L-13 (added 2026-10-19) |
