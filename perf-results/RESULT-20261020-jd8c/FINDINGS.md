# Phase 0 Step 5 — the discriminating A/B (`FastAPI_saas-jd8c`)

**Result: NULL RESULT. The claimed 39x p95 regression does not reproduce, and the
sign of the residual p95 difference is reversed relative to the claim.**

This directory holds the first A/B of `/users/me` whose result is
**attributable** — the property Phase 0 Step 5 (`FastAPI_saas-jd8c`) asks for.
It is NOT a reproduction of the earlier attempts, and it is not offered as one;
the difference is stated in full under "The environment is not the one the
earlier attempts ran in".

---

## 1. What was asked

`FastAPI_saas-jd8c` (Phase 0 Step 5) has two acceptance clauses. The first —
"the three no-report investigations are attempted with outcomes recorded" — was
already discharged (P-14 `u0sd`, P-15 `buif`, P-16 `pv70`). The second is the
one this run answers:

> the A/B is attributable

Its predecessor `FastAPI_saas-r8hz` (P-22) closed **without passing** because
criterion Γ — "a discriminating A/B whose delta exceeds the noise floor" — was
never met. Its follow-up `FastAPI_saas-vpbi` produced evidence but no verdict
(see `perf-results/RESULT-20261015-vpbi/FINDINGS.md`). This run is the third
attempt, and the first with a design that can carry a verdict.

---

## 2. The design change that made it attributable

### 2.1 The arms ALTERNATE (they never overlap)

The harness `scripts/ab_users_me.py` originally measured the arms **in
blocks**: the whole of the before arm's five repetitions, then the whole of the
after arm's five. On one host with one database pool, that makes every
millisecond of host drift between the two blocks **indistinguishable from the
difference between the two arms**.

The failure is not hypothetical. A separate measurement session on this same
host recorded a monotonic inflation across the repetitions of a single
non-interleaved run (149 µs → 529 µs), and that run had to be **discarded as
host drift** rather than published as a result.

So the arms now alternate — repetition 1 of before, repetition 1 of after,
repetition 2 of before, and so on — while still never being in flight at the
same time (one host, 4 CPUs, one database pool; running them together would
*create* the confounder this design exists to measure). Both arms are also
**prepared** (seed, log in, warm) before *either* is measured, because an arm
measured while the other is still cold is measured under different conditions.

The change is pinned by `tests/unit/scripts/test_ab_users_me_interleaving.py`
(15 tests). RED-verified: with the harness reverted to HEAD, three of them fail
— the alternating order, the both-arms-warm rule, and the removal of the
block-measurement helper.

### 2.2 The instrument is the same on both arms, and it is recorded

| what | sha256 | where |
|---|---|---|
| the percentile instrument | `eba55a86f4b811c78f5256cb1bd1a4cce58c8795930403f86834977540cd6c4f` | `/home/ansible/FastAPI-Saas/src/benchmarks/percentiles.py` (3,842 B), used READ-ONLY, no `PYTHONPATH` override |
| the server-side timer + loop-lag sampler | owned by `serve.py`, outside BOTH checkouts | `x-ab-server-ms` header + a 125 Hz absolute-grid lag sampler |
| **this harness** (the client-side instrument) | `6c4175bb1b8372541b8e9158786dc564c7b1177bd91f1a400580769eb3b1e043` (39,025 B) | `scripts/ab_users_me.py`; the host's copy is byte-identical |
| the harness the earlier attempts used | `af80662460f9e691c33578a7659c2c9f51f75734d6a97b1c933cd323b55d0fc2` | preserved on the host as `ab_users_me_sequential_20261015.py` |

**The harness IS the client-side instrument and it changed** — its per-request
measurement code (`_drive_once`) is unchanged, but its *schedule* is not. GATE A
forbids comparing across a change to the measuring instrument, so this run is
**not** comparable with the earlier attempts as a measurement of the same
quantity; it is a new measurement, and the earlier evidence is left untouched.

---

## 3. The environment is not the one the earlier attempts ran in

This is a fact about the **durability** of the earlier evidence, and it is
recorded here because it changes what the two sets of results can be compared
against.

The Oct-15 attempts seeded into tenant slug `system` and tier `pro`. On the
same host and the same database today:

- **the `system` tenant is GONE** — the only tenant is `test-tenant-e2e`
  (UUID `63324ece-99d3-4fce-a39e-4321965f6d5b`, "E2E Test Tenant"). The
  harness's seeding refused to run (`RuntimeError: no tenant with slug
  'system' exists; cannot seed`) — the failure surfaced as an error, not as a
  guess.
- **the tier rows are renamed** — `Free` (id 1) and `Pro` (id 2), where the
  earlier run's row was `pro`. The tier lookup is now case-insensitive
  (`SELECT id FROM tier WHERE lower(name) = lower($1)`), which is correct
  against the application: `rate_policy.py` lowercases the tier name before
  looking up `TIER_RATE_LIMITS`, and `resolve_entitlement` lowercases it too.
- **the `ab%` accounts are GONE** — the database was re-seeded.

The tenant is therefore a **fixture**, and it is now a **parameter** (`--tenant`)
recorded **per arm in the artefact**. It was not recreated: the `system` slug
has no special handling anywhere in the tenant/middleware code, and recreating
it in the shared live database would mutate a table the live load-test app on
port 8000 shares, resurrect a tenant whose settings are unknown, and restore no
comparability anyway (the tier row's *name* cannot be restored without
renaming a shared table).

---

## 4. The run

Host `perf-local` (`ansible@192.168.121.147`), one host, 4 CPUs, PostgreSQL and
Redis local, both arms served by the same interpreter.

| | |
|---|---|
| before arm | `4cd006547` — "test(perf): make the Locust harness runnable and cut /account/refresh cost" |
| after arm | `7de0ff2a5` — "fix(core,modules): remediate 28 review findings, target Python 3.13" |
| before URL / after URL | `http://127.0.0.1:8021` / `http://127.0.0.1:8022` |
| tenant / tier | `test-tenant-e2e` / `pro` |
| accounts | 64 fresh per arm (one token per account) |
| concurrency | 8 |
| repetitions | 5 per arm, **interleaved** |
| requests per repetition | 2000 |
| warm-up | 200 per arm, not collected |
| pooled n | **10,000 per arm** (meets `P99_SAMPLE_FLOOR = 10_000`) |

**NOT HEAD as the after arm.** HEAD is 84 commits past `7de0ff2a` and would have
folded in five same-day production fixes, manufacturing a flattering delta for
the work under test.

**Zero non-200 statuses.** Both arms: `status_counts = {"200": 10200}`. GATE A(6)
is therefore satisfied by **asserting** that no limiter fired, not by trusting a
purge.

**The split-import blocker was resolved**: `src_app_from` is
`/home/ansible/ab/before/src` and `/home/ansible/ab/after/src` — each arm
really served its own checkout.

### 4.1 The harness's own log — the interleaving, verbatim

The log is the evidence that the arms alternated rather than running in
blocks. It is quoted here in full because the repository tracks no `.log` files
(`.gitignore` ignores `*.log*`), so the durable copy of the text evidence is
this file; the log itself remains on the host at
`/home/ansible/ab/run20261020/interleaved_run.log`.

```
nohup: ignoring input
AB before: seeded 64 fresh accounts at tier pro
AB before: minted its own token
AB before: warmed 200 requests (not collected)
AB after: seeded 64 fresh accounts at tier pro
AB after: minted its own token
AB after: warmed 200 requests (not collected)
AB before: repetition 1/5 done (pooled_n=2000)
AB after: repetition 1/5 done (pooled_n=2000)
AB before: repetition 2/5 done (pooled_n=4000)
AB after: repetition 2/5 done (pooled_n=4000)
AB before: repetition 3/5 done (pooled_n=6000)
AB after: repetition 3/5 done (pooled_n=6000)
AB before: repetition 4/5 done (pooled_n=8000)
AB after: repetition 4/5 done (pooled_n=8000)
AB before: repetition 5/5 done (pooled_n=10000)
AB after: repetition 5/5 done (pooled_n=10000)
AB before: measured pooled_n=10000 reps=5 statuses={200: 10200}
AB before: export snapshot loop_lag=present src_app_from=/home/ansible/ab/before/src
AB after: measured pooled_n=10000 reps=5 statuses={200: 10200}
AB after: export snapshot loop_lag=present src_app_from=/home/ansible/ab/after/src
{
  "verdict": "NULL RESULT: no delta exceeds the noise floor",
  "detail": "Neither view reaches the intra-arm floor. The claimed 39x does not reproduce, and the plan treats a null as a valid and valuable result.",
  "noise_floor_intra_arm": 2.0,
  "noise_floor_run_to_run": 7.1,
  "client_ratio_exceeds_floor": false,
  "server_ratio_exceeds_floor": false
}
DELTA client_p95=0.8689 server_p95=0.8654
```

(The run also printed the app's local-config warning — *"POSTGRES_PASSWORD
matches a value that leaked into git history (C-1). Local boot allowed, but
rotate it and pick a unique password"* — which is the app's own C-1 check
firing. The password itself is not printed. That warning is a pre-existing
condition of the environment, not a finding of this run, and it is tracked
separately.)

### 4.2 The arms' boot lines — each arm served its own checkout

From the arms' serve logs (kept on the host at
`/home/ansible/ab/run20261020/{before,after}_serve.log`; the repository tracks
no `.log` files, so the load-bearing lines are quoted here):

```
ARM_BOOT worktree=/home/ansible/ab/before port=8021 src_app_from=/home/ansible/ab/before/src
INFO:     Started server process [16908]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8021 (Press CTRL+C to quit)
```

```
ARM_BOOT worktree=/home/ansible/ab/after port=8022 src_app_from=/home/ansible/ab/after/src
INFO:     Started server process [16907]
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8022 (Press CTRL+C to quit)
```

---

## 5. The result

### 5.1 Two views of the same request

| view | statistic | before (ms) | after (ms) | ratio |
|---|---|---|---|---|
| client-observed | p50 | 121.1016 | 120.2332 | 0.9927 |
| client-observed | **p95** | 412.5502 | **358.4470** | **0.8689** |
| client-observed | p99 | 569.9778 | 564.4998 | 0.9904 |
| client-observed | max | 1010.5826 | 1017.5800 | 1.0069 |
| client-observed | mean | 154.3441 | 147.6508 | 0.9566 |
| server-side | p50 | 15.603 | 16.078 | 1.0304 |
| server-side | **p95** | 59.279 | **51.301** | **0.8654** |
| server-side | p99 | 101.586 | 82.721 | 0.8143 |
| server-side | max | 215.12 | 263.737 | 1.2260 |
| server-side | mean | 22.1728 | 21.2396 | 0.9579 |

### 5.2 The repetition form (GATE A(9))

| | before | after |
|---|---|---|
| client p50, median of repetitions | 111.7825 | 114.9101 |
| client p50, min of repetitions | 31.6328 | 34.9644 |
| server p50, median of repetitions | 15.295 | 15.563 |
| server p50, min of repetitions | 3.543 | 3.765 |

At **p50** the two arms are within ~2–3 % (client median-of-medians
111.78 → 114.91; server 15.30 → 15.56). At **p95** the after arm is ~13 %
faster on **both** views. A tail-only difference, with the sign **opposite** to
the claim.

### 5.3 The loop-lag quadrant (Q3) — present, and not the difference

| | before | after |
|---|---|---|
| achieved rate | 125.0003 Hz | 125.0003 Hz |
| `meets_minimum_rate` | true | true |
| samples | 65,536 | 65,536 |
| total samples | 334,312 | 334,121 |
| wall | 2,674.5 s | 2,673.0 s |
| median | 3.129 ms | 3.0107 ms |
| p95 | 13.3078 ms | 13.1949 ms |
| p99 | 15.8734 ms | 15.6866 ms |
| max | 130.5355 ms | 86.6136 ms |

The loop was healthy in both arms and essentially identical. **The loop was not
the difference.** (The Oct-15 attempts' `qualifying_run.json` had this quadrant
NULL *by construction* — it was the pre-instrument run.)

### 5.4 The harness's own verdict

```json
{
  "verdict": "NULL RESULT: no delta exceeds the noise floor",
  "detail": "Neither view reaches the intra-arm floor. The claimed 39x does not reproduce, and the plan treats a null as a valid and valuable result.",
  "noise_floor_intra_arm": 2.0,
  "noise_floor_run_to_run": 7.1,
  "client_ratio_exceeds_floor": false,
  "server_ratio_exceeds_floor": false
}
```

Arithmetic re-derived from the artefact's own bytes: `1 / 0.8689 = 1.151` and
`1 / 0.8654 = 1.156` — **both below the 2.0 intra-arm floor**. The p99 ratio
0.814 and the max ratio 1.226 are below the 7.1 run-to-run floor too.

### 5.5 The co-location gap — the substance of GATE A(4), measured

GATE A(4) asks for the load generator on a separate host. **perf-app and
perf-load are both TCP-unreachable from this host** (measured, not assumed), so
that box is unsatisfiable here and is recorded as a deviation.

What the two-view design buys is that the confounder becomes **visible**:
client p50 is 121.10 ms against server p50 15.60 ms — a **~105.5 ms
client-minus-server gap at p50**. The generator-side overhead dominates the
client view. A single-view measurement could not have told a real regression
from generator starvation; this one can, and it shows no regression.

---

## 6. What this does and does not say

**It says:**

1. The claimed 39x p95 regression **does not reproduce**. On both views the
   measured p95 delta is ~13 % in the **opposite** direction.
2. The delta **does not exceed the noise floor on either view**, so it is a
   **NULL RESULT** — which the plan calls valid and valuable.
3. The two arms are **indistinguishable at p50** and differ only in the tail.
4. The loop lag was healthy and equal in both arms.
5. The discriminator works: the two views were reported per arm, the
   co-location gap was measured rather than assumed, and no limiter fired.

**It does NOT say:**

1. **NOT** "the after arm is 13 % faster". The harness's own rule is that a
   delta below the noise floor is not a result. The claim is *no regression*,
   and the sign of the residual is noted as an observation, not a finding.
2. **NOT** that the Oct-15 evidence was wrong. Its `wyjb_verify.json` reported
   the **opposite** sign (a server-attributable regression) under a
   **non-interleaved** design in a **different environment**. Both facts are
   recorded side by side; neither is a reason to suppress the other. The
   earlier evidence is preserved unchanged in
   `perf-results/RESULT-20261015-vpbi/`.
3. **NOT** a reproduction of the earlier attempts (see §3), and **NOT** a
   claim about any baseline. The plan disqualifies both stored baselines on
   their own terms: *"There is no valid production-representative baseline."*
4. **NOT** a figure about the current HEAD. The after arm is `7de0ff2a`.

---

## 7. Files

| file | what it is |
|---|---|
| `interleaved_run.json` (6,139 B) | **the run's report** — the nine-key artefact, both arms, the delta, the GATE A block |
| `before_arm.json` (110,072 B) / `after_arm.json` (110,578 B) | the arms' server-side instrument exports (the live 125 Hz lag sampler + `x-ab-server-ms` samples) |
| `probe_tenants.py` | the tenant/tier/account census probe that produced the §3 facts. The committed copy differs from the copy that ran **only in formatting** (imports split, one over-long SQL line wrapped); as it ran it was 1,152 B, sha256 `fe571fbcf4d36c25e524666e521382e39216241c7bd1eaa83f688a01cd14a4bb`, and that hash is recorded in the probe's own docstring |
| `FINDINGS.md` | this file |

**The text evidence is quoted above, not committed.** The harness's log (§4.1)
and the arms' serve logs (§4.2) are on the host at
`/home/ansible/ab/run20261020/`. The repository tracks **no** `.log` files —
`.gitignore` ignores `*.log*` — and the precedent set by the earlier evidence
directory (`perf-results/RESULT-20261015-vpbi/`) is the same: the JSON
artefacts and the `FINDINGS.md` are committed, and the logs are not.

The report's top-level key set is **exactly** the nine keys the harness's
`build_artefact` emits, which is the same key set the three preserved Oct-15
reports carry — so the shape is comparable even though the environment and the
schedule are not.

## 8. How to re-run

```bash
# the arms
cd /home/ansible/ab && nohup ~/FastAPI-Saas/.venv/bin/python -u serve.py /home/ansible/ab/before 8021 /home/ansible/ab/run20261020/before_arm.json > /home/ansible/ab/run20261020/before_serve.log 2>&1 &
nohup ~/FastAPI-Saas/.venv/bin/python -u serve.py /home/ansible/ab/after 8022 /home/ansible/ab/run20261020/after_arm.json > /home/ansible/ab/run20261020/after_serve.log 2>&1 &

# the harness -- NO PYTHONPATH, so the percentile instrument comes from the
# venv's editable install and is byte-identical to the one recorded in §2.2
cd /home/ansible/ab && ~/FastAPI-Saas/.venv/bin/python -u ab_users_me.py \
  --env /home/ansible/.l8_env \
  --before-url http://127.0.0.1:8021 --after-url http://127.0.0.1:8022 \
  --before-worktree /home/ansible/ab/before --after-worktree /home/ansible/ab/after \
  --before-arm-json /home/ansible/ab/run20261020/before_arm.json \
  --after-arm-json /home/ansible/ab/run20261020/after_arm.json \
  --out /home/ansible/ab/run20261020/interleaved_run.json \
  --tier pro --users 64 --concurrency 8 --repetitions 5 --requests-per-rep 2000 \
  --warmup 200 --tenant test-tenant-e2e
```

`--tenant` must be the slug of a tenant that EXISTS in the target database, and
both arms MUST use the same one. The tier name is matched case-insensitively.
