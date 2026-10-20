# Lane C: where the work of the five beads whose record said "Closed" landed

**Bead:** `FastAPI_saas-94os` — "Lane C: recover and record the evidence of the
four e2e beads and the C-4 implementation bead, whose close_reasons are literally
'Closed'".

**The five beads this record covers**

| Bead | Item |
|---|---|
| `FastAPI_saas-uu5p` | C-1 e2e — the scope-`path` HTTP probe |
| `FastAPI_saas-dxra` | C-3 e2e — the three 401 shapes |
| `FastAPI_saas-vshp` | C-32 e2e — the blacklist session probe |
| `FastAPI_saas-levg` | C-4 e2e — the job-metrics pipeline |
| `FastAPI_saas-vmv0` | C-4 implementation — the job-monitoring module |

## What this document is

A record of **where the work landed**, recovered from the git object database,
for five beads whose own close_reason is the single word `Closed`.

It is a record of **provenance**, not of correctness. It says which commit
introduced each artefact and what that commit's message says. It does not re-run
the work, does not re-check the artefacts, and does not say that anything the
work concluded is correct.

## The finding, in one paragraph

Four of the five beads' artefacts — the C-1, C-3 and C-32 e2e scripts and the
C-4 e2e artefact — **and** the C-4 implementation's production change all
arrived in one commit whose subject is

> `chore: carry the work-in-progress left in the tree by earlier sessions`

That is a **work-in-progress carry**, not a bead commit: a commit that swept up
whatever was in the working tree, without naming the work it carried. The fifth
artefact, the C-4 e2e **script** `scripts/e2e_job_metrics_pipeline.py`, is the
one exception — its own commit names "C-4 e2e" in the subject and it changed
that one file and no production code.

So the evidence for these five beads is not a set of five bead commits. It is
one carry commit plus one script commit. That is why their close_reasons said
nothing: there was, at the time, nothing per-bead to point at.

## How it was found

The bead id **cannot** be used to search for the commits: no commit message in
the reachable history names `FastAPI_saas-uu5p`, `-dxra`, `-levg`, `-vshp` or
`-vmv0`. The search that works is **by path**:

```
git log --all --diff-filter=A --format='%H|%s|%ad' --date=short -- <path>
```

for each artefact, plus `--diff-filter=M` for the production module. The
per-file results are in the table below; the commit bodies follow it verbatim.

| Artefact | Introducing commit | Subject | Date |
|---|---|---|---|
| `tests/e2e/feature_validation/test_c1_scope_path_http_probe.py` | `ec632f29` | chore: carry the work-in-progress left in the tree by earlier sessions | 2026-10-17 |
| `tests/e2e/feature_validation/test_dxra_three_401_shapes.py` | `ec632f29` | chore: carry the work-in-progress left in the tree by earlier sessions | 2026-10-17 |
| `tests/e2e/feature_validation/test_c32_blacklist_session_probe.py` | `ec632f29` | chore: carry the work-in-progress left in the tree by earlier sessions | 2026-10-17 |
| `perf-results/RESULT-20261016-c4e2e/c4_job_metrics_e2e.json` | `ec632f29` | chore: carry the work-in-progress left in the tree by earlier sessions | 2026-10-17 |
| `src/app/core/job_monitoring.py` (the C-4 implementation's module) | `ec632f29`, `f5452db2` | chore: carry the work-in-progress left in the tree by earlier sessions | 2026-10-17 |
| `scripts/e2e_job_metrics_pipeline.py` (the C-4 e2e script) | `2fc6a7e7` | test(perf): C-4 e2e - the job-metrics pipeline returns the same list in the same order | 2026-10-16 |

`src/app/api/v1/admin/jobs.py` was **not** touched in October (its last change is
`82c4855c`, 2026-09-20), so it is not the C-4 implementation's module.

## The commit bodies, verbatim

Nothing below is paraphrased. The bodies are reproduced exactly as
`git show -s --format=%B <sha>` renders them.

### ec632f29 — the work-in-progress carry (2026-10-17)

```text

chore: carry the work-in-progress left in the tree by earlier sessions

These changes predate the API-key work and were already in the working tree;
they are committed here as-is, unreviewed by the session that added them, so
the tree starts from a known state rather than a mixed one. Grouping them in one
commit is deliberate: splitting work this session did not author would only
manufacture an attribution it cannot support.

Themes, read from the diff, not from any bead:
- session invalidation and logout row-set probes (tests/e2e/feature_validation
  test_s1..s6, the c32 blacklist-session and dxra three-401-shapes probes, the
  s01 oracle, tests/conftest.py and tests/e2e/conftest.py fixtures)
- api-key and composite-prefetch work in src/app/api/deps/auth.py with its unit
  tests, RBAC dependency coverage, the principal and session_manager seams
- job monitoring pipeline plus the broad-except ratchet and the scan-cost
  benchmark
- ADR-0008 (attack-scan bound) and src/app/middleware/attack_detection.py
- the Phase-0 measurement artefacts of the 20261016 beads
  (RESULT-20261016-bcrypt, -c4e2e, -l12, -l13, -m1, -m6) and their probe scripts

The LSP in this environment cannot resolve src.app imports and reports the new
API_KEY_LIMIT / build_api_key_rate_limit_key names as unknown on the oracle
file; uv run mypy src/ and pytest are the authorities and both are green.

```

### 5452db2 — the second work-in-progress carry (2026-10-17)

```text

chore: carry the work-in-progress left in the tree by earlier sessions

These changes predate the API-key work and were already in the working tree;
they are committed here as-is, unreviewed by the session that added them, so
the tree starts from a known state rather than a mixed one. Grouping them in one
commit is deliberate: splitting work this session did not author would only
manufacture an attribution it cannot support.

Themes, read from the diff, not from any bead:
- session invalidation and logout row-set probes (tests/e2e/feature_validation
  test_s1..s6, the c32 blacklist-session and dxra three-401-shapes probes, the
  s01 oracle, tests/conftest.py and tests/e2e/conftest.py fixtures)
- api-key and composite-prefetch work in src/app/api/deps/auth.py with its unit
  tests, RBAC dependency coverage, the principal and session_manager seams
- job monitoring pipeline plus the broad-except ratchet and the scan-cost
  benchmark
- ADR-0008 (attack-scan bound) and src/app/middleware/attack_detection.py
- the Phase-0 measurement artefacts of the 20261016 beads
  (RESULT-20261016-bcrypt, -c4e2e, -l12, -l13, -m1, -m6) and their probe scripts

The LSP in this environment cannot resolve src.app imports and reports the new
API_KEY_LIMIT / build_api_key_rate_limit_key names as unknown on the oracle
file; uv run mypy src/ and pytest are the authorities and both are green.

```

### 2fc6a7e7 — the C-4 e2e script commit (2026-10-16)

```text

test(perf): C-4 e2e - the job-metrics pipeline returns the same list in the same order

vmv0 replaced the serial N x HGETALL loop in get_job_metrics with one
pipeline(transaction=False) per SCAN batch. Nothing proved the two agree.
This script carries the equivalence burden, because no measured admin-path
baseline exists anywhere for it.

Four assertions, checked where each is actually observable:

(a) Element-for-element AND in order. Production is compared against a
    mapping the script derives from its own fixture - never read back out of
    production - and against _serial_reference, a reimplementation of the
    pre-C-4 loop. list(keys) order is compared too, and the run records that
    the scan order was unsorted so that comparison is not vacuous.

(b) MULTI/EXEC absent, asserted on both sides: the recorded transaction
    kwargs are [False], and the INFO commandstats delta carries no multi and
    no exec while showing all 8 HGETALLs executed. Exact because the perf
    Redis is dedicated.

(c) Round trips against a ceiling stated as a rule, not a literal:
    scan_calls + 1, versus scan_calls + fixture_size before C-4. Observed 2
    against a serial loop that was run to produce 9.

(d) _parse_metrics input unchanged, by digesting what it receives and
    comparing against digests of the script's own fixture. The per-function
    label is what makes mis-pairing visible; the response model exposes only
    five numeric fields, so pairing cannot be proven through HTTP.

Plus the HTTP layer over GET /api/v1/admin/jobs/metrics, logged per the
avpc spec: request id, method, path, status, assertion, expected, observed.
X-Response-Time does not exist in this codebase, so the server clock is
recorded as absent rather than substituted.

Two findings from getting this to green are worth keeping:

The first fully green Part 1 was VACUOUS. The copy of job_monitoring.py on
the Linux target was still the pre-C-4 serial loop, so the serial reference
was being compared against serial production, which agree trivially. Only
the transaction-kwarg assertion caught it: the kwargs were [] because
production never opened a pipeline. b_production_under_test_is_the_pipelined_form
now greps the loaded module and the summary records production_source_sha256,
so a run cannot certify the wrong build again.

Part 2's order comparison was asserting a dead fallback. part_one and part_two
each built their own result dict, so result.get("scan_order", sorted(...))
always fired and invented its own expectation. It now indexes the key. Part 2
gates the key SET rather than the order, because SCAN order is not a Redis
contract and a gate that can fail for unrelated reasons is worse than none;
the observed orders are published as a non-gating observation, and they match.

Exit 0, 16 assertions, zero failures. The saving is density-dependent and was
observed to range from 1 to 7 round trips across runs, so it is a property of
the keyspace, not a constant. Low priority: this is an admin path, not a hot path.

```

## What this record is NOT

- **It is not a re-run.** Nothing here was re-measured, re-executed or
  re-verified. The artefacts were located, not re-validated.
- **It is not an endorsement.** The WIP-carry commit `ec632f29` swept up more
  than twenty files — production changes under `src/app/api/deps/auth.py`,
  `src/app/core/principal.py`, `src/app/core/rbac/dependencies.py`,
  `src/app/core/security/session_manager.py`,
  `src/app/core/utils/lua_manager.py`, `src/app/core/monitoring/process_series.py`,
  several `scripts/` probes, seven `perf-results/` artefacts and the ADR-0008
  annotation, among them. **"The commit that landed it" is not "the commit that
  proved it."** A reader who wants to know whether the C-1/C-3/C-32/C-4 work is
  sound must read the artefacts and the diffs, not this record.
- **It does not repair the carry.** A WIP-carry commit is a bad shape for
  provenance and this record does not pretend otherwise; it only makes the shape
  visible and names it.

## The corrections made to the five bead records

Each of the five beads now carries a dated amendment in its notes, added with
`bd update <id> --append-notes`, naming the commit that landed its artefact and
the artefact's path. The amendments record what the commit was, not a claim
about the work. The tracker mechanism is the one `FastAPI_saas-8oyv` blesses for
correcting a closed bead's record: a dated amendment appended to the bead, so
the correction is findable from the bead itself.

## The standing debt this does not discharge

The material these beads' work was recovered from — the plan and audit
documents under `reports/` and `PLAN_FOR_ADVANCED_*` — is still **unversioned**:
`.gitignore:192` ignores `PLAN_FOR_ADVANCED_*` and `.gitignore:234` ignores
`AUDIT_*.md`. That debt is owned by `FastAPI_saas-fwmf`. Until it is discharged,
this record is the only tracked index of that work.

**No test was weakened to accommodate this record; no assertion was relaxed.
This document changes no behaviour and asserts no number.**

### Note on the commit identities (2026-10-20)

`ec632f29` - the work-in-progress carry that introduced four of the five
files in the table above - **is** an ancestor of this branch's tip and can be
read with `git show ec632f29`. `f5452db2`, the second carry that touched
`src/app/core/job_monitoring.py`, is **not**: it is contained by no branch or
ref, so it survives in this repository's object database only, and a fresh
clone will not contain it. The file it touched is in the tree, and this
record is the provenance of that file's origin.
