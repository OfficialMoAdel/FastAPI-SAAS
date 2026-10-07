# ADR-0021: amxv deferred — the dedicated Linux harness job needs two hosts

**Status:** Deferred — NOT rejected
**Date:** 2026-10-07
**Tracked by bead:** `FastAPI_saas-amxv` — "No CI job runs the rebuilt harness on Linux, so qk6z and i1jy evidence is unobtainable"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>

## Decision

**`FastAPI_saas-amxv` is DEFERRED, not rejected.** It closes as deferred with
this ADR as the record.

- **Status:** deferred.
- **Reason:** infrastructure dependency — a **self-hosted runner pair**. The job
  as specified cannot be built on any GitHub-hosted runner, and provisioning a
  runner fleet is not work an agent can perform.
- **Re-open condition:** a Linux two-host Phase-0 environment is provisioned by
  the infrastructure team.
- **Prerequisite work continues:** harness repair (H-1 through H-11), telemetry
  additions (T-1 through T-8), and live defect fixes (F-12, J9) are **not**
  blocked by this deferral and proceed independently.
- **Not rejected:** amxv remains a Phase-0 requirement per the unified plan §4
  and the platform addendum §5.1 (A-1, A-9).

## Context

amxv asked for a CI job that runs `tests/integration/performance/` on Linux and
proves the perf harness works there. Most of that has been delivered; one
element is blocked by infrastructure rather than by effort.

`scripts/run_perf_baseline.sh` refuses, **by design**, to measure a server bound
on the host it runs on. Its own header states there is "no configuration of this
script that measures a server on this host, which is the only way to make the
refusal below something other than a promise", and its `--host` validation
refuses "a loopback address, this host's name, or any address bound on this
host". Binding `0.0.0.0` and then targeting the runner's own private IP does not
help: the rule is about where the address is **bound**, not how it is spelled.

That refusal is correct and must not be weakened. It is what stops a Windows or
loopback run from being reported as a Linux production measurement. Satisfying
amxv's load-measurement clause therefore requires **two real hosts**, which means
a self-hosted runner pair — infrastructure the repository does not have.

## What was already delivered (and observed, not argued)

A dispatch-only job in `.github/workflows/tests.yml`, gated on
`github.event_name == 'workflow_dispatch'` so ordinary pushes do not pay for it.
Observed on `ubuntu-latest` in dispatch run `37637419697`, all jobs successful:

- The suite runs on Linux and **29 tests execute**; the job asserts a test id is
  `PASSED`, and that assertion was proven to fail against a `SKIPPED` line, so a
  silently-skipped suite cannot report success.
- The five runtime-identity fields are read off a **real running Linux server**,
  with uvloop and httptools confirmed in the live process's
  `/proc/<pid>/maps` — not merely "resolved", which is the limitation the
  resolver's own docstring admits.
- Manifests and reports upload as artefacts; the run is labelled a shared runner
  in the job name and the step summary and satisfies no Gate A checkbox.
- `tests/integration/performance/test_baseline_runner_e2e.py` gained a real
  assertion class in place of a self-skipping placeholder.

## Consequences

- `FastAPI_saas-i1jy` is discharged by the above and closed.
- `FastAPI_saas-qk6z` is discharged by the above and closed.
- The throughput/p95 number the programme's capacity claims rest on is still
  **unmeasured**. No number from the shared-runner dispatch job may be quoted as
  a capacity result; that is why the job's step summary says so explicitly.
- `_phases.json` cannot exist until the two-host environment does, because it is
  written by the load phase that requires it.

## What is explicitly NOT being traded away

Deferring the job does not license publishing an unsourced number, weakening the
runner same-host refusal, or satisfying a Gate A checkbox from shared-runner
data. Each of those would convert a blocked measurement into a false one, which
is the failure this programme's addenda exist to prevent. The measurement stays
blocked until two hosts exist.