# Measurement Discipline and Unprofiled Paths

This page records how Round 2 reads the existing performance evidence and which
paths are explicitly **not** optimized from it. It is a decision record, not a
new benchmark result.

## Read m6 correctly

`perf-results/RESULT-20261016-m6/m6_linux_request_profile.json` is a flat profile:

- The top five frames account for **2.64%** of CPU in the primary run and
  **2.62%** in the replicate. The largest single frame is **0.89%**.
- The top frames are four log-sanitizer frames plus attack-detection pattern
  matching, not request-serving logic.
- The artefact's own `acceptance_criterion` says it **FAILS THE CONCENTRATION
  CRITERION** and that the profile is **not accepted as a decomposition of the
  request**. Its `plan_rewrite.status` is `REQUIRED`, and it says optimisation
  targets may **not** be drawn from the cProfile ranking.

The separate `perf-results/CPU_PROFILE.md` run reports a 2.8% top-five share and
a 1.1% largest frame. Those are **not** m6 numbers; cite the document that
produced a number.

### Conclusions this evidence supports

- GET `/users/me` has no dominant CPU hotspot in the measured profile.
- The dominant admissible cost is Redis round trips. m6 measured **3.00**
  commands per GET and **10.01** per refresh.
- The GET guard stack is **42.2%** of wall clock, but that time is mostly Redis
  wait, so removing CPU instructions from the guard stack is not the lever.
- Round 2 should reduce round-trip counts, not micro-optimize guard CPU.

### Conclusions this evidence does not support

Do **not** conclude that no CPU work remains anywhere in the system. m6 covers
GET and refresh only. It says nothing about webhook fan-out, audit flushing,
notification rendering, or the ARQ tick.

### Current-code correction

m6's `3.00` GET count predates the P0-2 fusion. On current HEAD:

- authenticated requests use `check_ban_blacklist_and_rate_limit`, which executes
  the `ban_blacklist_gcra_check` Lua (blacklist + ban + GCRA in one call);
- the anonymous/legacy path still uses `check_ban_and_gcra_check`.

Re-measure HEAD before putting a specific command count into a budget. The
operator harness is `src/benchmarks/route_command_count.py`; it reports the exact
server-side delta for one request and fails when the count exceeds the declared
ceiling.

## Evidence labels

Every performance comparison and optimisation claim must identify its source:

| Source | What it measures |
|---|---|
| `perf-results/RESULT-20261016-m6/m6_linux_request_profile.json` | 2026-10-16 GET/refresh profile and I/O census |
| `perf-results/CPU_PROFILE.md` | 2026-10-12 25-request profile; older and separate from m6 |
| `src/benchmarks/route_command_count.py` | current-HEAD Redis command count for one route request |
| `src/benchmarks/s1_session_invalidate_roundtrips.py` | current-HEAD session-invalidation command count |

An optimisation claim must also state its effect on the Redis round-trip count,
even when the change is CPU-only. A CPU-only change that does not move request
cost is not a Round-2 win.

## Paths m6 does not cover

m6 profiles GET `/users/me` and refresh. These four paths are out of scope for
its conclusions and are **not** changed by Round 2:

| Path | Why it is not a Round-2 target | What a focused profile must isolate |
|---|---|---|
| Webhook fan-out (`WebhookService.trigger_event`) | Asynchronous, endpoint-count dependent, and off the request path | SQL reads for active endpoints, bulk insert, and the per-delivery enqueue cost at 1/10/100 endpoints |
| Audit-log flush (`flush_audit_log_buffer`) | Worker batch path, not a request | Redis `LRANGE`/`LTRIM`, parse/validate cost, and insert throughput per batch size |
| Notification rendering (`NotificationService.render_template`) | Template cache hit and tenant-override miss are different paths | A focused warm-render cProfile (1,000 renders of `welcome.html.jinja2`, dev box) is CPU-bound in template execution and HTML escaping; the content fetch is a separate cache/DB path |
| ARQ tick (`flush_audit_logs_task` and the worker cron) | Needs a running worker and queue | Queue-to-start latency, per-job CPU, and the cron interval's effect on backlog |

**Why no profile is required for this phase:** Round 2 makes no optimisation to
any of these paths, so a profile would have no decision attached to it. The
notification render spot check above does not justify a change; it only confirms
that the warm render path is template/escaping CPU, not I/O. These paths must be
profiled before a future change claims a win there; drawing a target from a
request-path profile would repeat the mistake m6 itself withdrew from.

The `EmailStr` validation in `UserRead.email` is deliberately retained. It is the
public contract's validation floor, and the double-validation pass was already
removed (pinned by `tests/unit/modules/users/test_users_me_email_validated_once.py`).
The measured cost is about **4.18%** of request p50 (172.507 us of 4225.224 us in
`perf-results/RESULT-20261018-2h3n/`), not 6%. The exact percentage does not
change the decision: do not remove the validator to chase it.
