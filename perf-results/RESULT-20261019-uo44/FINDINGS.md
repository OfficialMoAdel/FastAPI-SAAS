# S-8: the attack-scan method gate's debug events, counted per level

Bead: `FastAPI_saas-uo44` (S-8, Lane B, test-correctness / capacity-lever).
Artefact: `s8_logger_call_counter.json` in this directory.
Driver: `src/benchmarks/s8_logger_call_counter.py` (in the tree).
Oracle: `tests/unit/benchmarks/test_s8_logger_call_counter.py` (25 tests).

## What was asked for, and what could honestly be delivered

S-8's inherited standing rule (from `ic9r`, whose close recorded the
deliverables as NOT delivered — "need Linux target + live run") asked for
two things:

* **(a)** an END-TO-END script over HTTP against a running app on the Linux
  target, logging per step: request id, status code, resolved latency, and
  which assertion is being checked;
* **(b)** "the microbenchmark or round-trip counter that substantiates the
  capacity claim, logged in full".

**(b) is delivered here. (a) is NOT.** It is still open, and it is the part
that genuinely needs a live server; see *What is still open* below.

The reason (b) is a COUNTER rather than a microbenchmark is S-8's own
arithmetic, quoted in the bead: the guard and the event it suppresses are

```
0 us - 0.00 % of the 4.09 ms uncontended floor and 0.00 % of the 740 ms p95
```

A latency figure cannot substantiate a claim whose own value is zero: there
is no duration to report, and any number a microbenchmark produced would be
a statement about the harness rather than about the call site. What CAN be
substantiated is a **count**: how many times the middleware evaluates the
guard, and how many times it invokes `logger.debug`, on a bodyless GET, at
each effective level. A count is a structural property of the code and the
configuration, not a timing of a machine, so the same counts come out on any
host — which is why the artefact records `host_relevant: false`.

This is the same shape as L-12 and L-13 (see their `FINDINGS.md`): a claim
whose quantity is not a duration has to be measured with an instrument that
measures that quantity, and the instrument has to be in the tree so anyone
can re-run it.

## The measurement

Three arms over the **real** middleware and the **real** logging stack. The
arms differ ONLY in the effective level of the module's stdlib logger
(`src.app.middleware.attack_detection`):

| arm | module logger | effective level | predicate | guard evaluations | `logger.debug` calls | events |
|---|---|---|---|---|---|---|
| `debug` | DEBUG | DEBUG | **true** | **25** | **25** | `attack_detection_skipped_body_scan_for_method` |
| `info` | INFO | INFO | **false** | **25** | **0** | — |
| `default` | NOTSET | INFO (the root's) | **false** | **25** | **0** | — |

25 bodyless, benign GETs per arm through `AttackDetectionMiddleware.__call__`
with a stub downstream application. Root level `INFO`; the log level is
pinned by the harness's `bootstrap()` and recorded in the artefact.

**The invariant that makes the comparison mean something** is the
`guard_evaluations` column: the guard is evaluated once per request in
EVERY arm. If it were not, a difference in the debug count could be
explained by the call site being reached a different number of times rather
than by the predicate's value. The two counters are therefore separate on
purpose — the guard is counted on the bound logger's own `is_enabled_for`
(filtered to this module's logger, so the *guard* is counted and not the
stdlib's internal level check that follows an emitted record), and the call
site is counted on the module proxy's own `debug` method. A single counter
could not tell "the guard closed" from "the call site was never reached".

The harness **refuses to publish** a run whose arms disagree on the
evaluation count, whose debug count contradicts its own predicate, or whose
`debug` arm's predicate is not True / `info` arm's is not False. The oracle
executes those refusals on doctored runs, so the refusals are not a claim
in a docstring.

### Why `setup_logging()` runs first

`is_enabled_for` only consults the effective stdlib level because A-3
(`FastAPI_saas-49yx`) configured structlog with this repository's own
`LevelAwareBoundLogger`. structlog's ambient default is
`BoundLoggerFilteringAtNotset`, whose predicate is structurally always True
— against that default, **every** arm would report a debug call per request
and the counter would be measuring structlog's default rather than this
repository's logging stack. The harness therefore installs the repository's
real configuration before counting, and records it in the artefact's
`configuration` block.

## What changed in the code, and what did not

**Changed:** one test was added to
`tests/unit/middleware/test_attack_scan_method_gate.py` —
`test_debug_guard_is_level_aware_at_debug`. S-8's acceptance criterion says
"the real predicate is asserted at DEBUG and at INFO", and only the INFO half
was asserted explicitly (`is_enabled_for(DEBUG) is False`). The DEBUG half
is what distinguishes a **delegating** predicate from a **frozen-minimum**
one: `structlog.make_filtering_bound_logger(logging.INFO)` returns a
`BoundLoggerFilteringAtInfo` whose `is_enabled_for(DEBUG)` is also False —
the same value, for a completely different reason. A predicate that is False
at INFO and False at DEBUG has closed the gate in both directions and would
silently drop the skip event for the operators who asked for it.

**Not changed:** the middleware, the logger, `docs/adr/0008`,
`scripts/s7_scan_cost_probe.py` and the existing method-gate tests are all
untouched. The predicate behaviour A-3 landed is what the counter measures;
nothing here changes it.

## What is still open

* **Deliverable (a) — the END-TO-END-over-HTTP script on the Linux target.**
  Not delivered. It needs a running app and is a handful of requests rather
  than load generation, so it is a separate piece of work, not a rounding
  error in this one.
* The capacity claim itself is unchanged by this work: S-8 moves capacity,
  not p95, and the arithmetic above stands.

## Limits (carried in the artefact as well)

* This is a **count**, not a latency measurement. It substantiates a
  structural property of the call site, not a performance figure.
* The requests are driven **in-process** through the middleware's ASGI
  `__call__` with a stub downstream application, so the counter exercises the
  middleware and not the whole application stack.
* The middleware's own body scan and detection paths are **not** exercised:
  the requests are benign by construction, which is what makes the skip the
  only event in play.
* The counter is meaningless against structlog's ambient default (see above).
* The counter calls **no clock**, so it is not classified as a microbenchmark
  by `tests/unit/benchmarks/test_microbench_protocol.py` and is deliberately
  absent from that gate's `ALLOWED_RAW_TIMERS` list — the oracle asserts both
  facts, so the list cannot quietly grow an entry for a harness that has no
  timing in it.

## How to re-run

```bash
uv run python src/benchmarks/s8_logger_call_counter.py --requests 25
```

The default output is `reports/s8_logger_call_counter.json`, and
`reports/*.json` is gitignored (`.gitignore:188`), so a re-run cannot
overwrite the tracked copy in this directory. The tracked copy is the
record.

## File inventory

| file | what it is |
|---|---|
| `s8_logger_call_counter.json` | the recorded artefact: 25 requests per arm, the three arms, the verdict, the limits |
| `../FINDINGS.md` | this note |
