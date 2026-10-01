# Source-text test assertions in the performance trees

Inventory and classification of every test assertion under `tests/performance/`
and `tests/e2e/performance/` whose truth is decided by the literal characters
of an implementation file, plus the conversion of the densest batch.

**Baseline: 75 source-text assertions across 40 tests in 10 files.**
**After conversion: 44 assertions across 26 tests in 8 files (−31).**

## What counts as a source-text assertion

An assertion is source-text when it reads the implementation and decides the
test result from the text. Three mechanisms were found:

| Mechanism | Example |
|---|---|
| `inspect.getsource(...)` + substring | `assert "await self.db.refresh(tenant)" in source` |
| `__doc__` substring | `assert "session refresh" in TenantService.update_tenant.__doc__` |
| `source.find(...)` offset compare | `assert source.find("await self._prepare_turn(") < source.find("self._invoke_chat(")` |

Counts were produced with an AST pass (`getsource` → bound name → `assert`),
including `.find()` offsets and `X or ""` propagation, so the totals below are
dataflow-derived, not grep approximations. Three earlier grep-based passes
under-counted (66 → 72 → 75) because they missed the `.find()`-derived
ordering assertions in `test_pool_behavior.py` and the `__doc__` reads in
`test_returning_clause.py`.

### Why this class of assertion is a defect

A source-text assertion breaks in both directions at once:

- **Fails on harmless change.** Routing a commit through a helper, renaming a
  local, or splitting a method changes the text while behaviour is identical.
- **Passes on the defect.** `assert "await self.get_tenant(tenant_id)" not in
  source` is satisfied by `await self.get_tenant(tid)`. A substring ban is
  evaded by any rename, alias, or reformat.

The second failure mode is the dangerous one: these tests report green while
the regression they name is present. Rewriting them to behaviour is not
cosmetic — it is what makes them able to fail.

## Inventory (baseline)

`source-text assertions` / `tests carrying them`, densest first.

| # | File | Assertions | Tests | Verdict |
|---|---|---|---|---|
| 1 | `tests/performance/test_optimizations_r4/test_returning_clause.py` | 20 | 11 | **rewrite** (converted) |
| 2 | `tests/performance/test_pool_behavior.py` | 11 | 3 | **rewrite** (converted) |
| 3 | `tests/e2e/performance/perf_validation_r4.py` | 9 | 5 | rewrite (e2e, deferred) |
| 4 | `tests/e2e/performance/test_opus_optimizations_e2e.py` | 9 | 5 | delete + rewrite (deferred) |
| 5 | `tests/performance/test_optimizations_r4/test_direct_dicts.py` | 6 | 3 | rewrite (duplicate of #1) |
| 6 | `tests/performance/test_optimizations_r4/test_event_bus_parallel.py` | 5 | 3 | delete (subsumed) |
| 7 | `tests/performance/test_optimizations_r4/test_redis_pool.py` | 5 | 3 | rewrite |
| 8 | `tests/performance/test_optimizations_r4/test_cache_bytes.py` | 4 | 2 | delete + rewrite |
| 9 | `tests/performance/test_optimizations_r4/test_ai_streaming_json.py` | 3 | 2 | delete (subsumed) |
| 10 | `tests/performance/test_rollback_verification.py` | 3 | 3 | rewrite |
| | **Total** | **75** | **40** | |

No other file in either tree contains source-text assertions
(`test_benchmarks.py`, `test_equivalence.py`, `test_rls_performance.py`,
`test_rbac_perf.py`, `test_tenant_performance.py`,
`test_rate_limit_behavior.py`, `test_resilience_benchmarks.py`,
`test_metrics_benchmarks.py`, `test_concurrent_load.py`, the `benchmarks_r4*`,
`baseline_*` and `locustfile.py` harnesses are clean). The single
`inspect.getsource` mention in `test_cache_warming.py:192` is inside a
docstring describing a scan that was **already** replaced with a behavioural
assertion — a precedent for this conversion, and the reason that file is
classified *legitimate*.

### Classification criteria

- **rewrite** — the assertion names a real contract that survives the
  conversion; the property is observable at a seam.
- **delete** — the property is already covered behaviourally elsewhere, or the
  assertion only pins a spelling/comment that no consumer depends on.
- **legitimate-structural** — the assertion is a *shape* check (function
  exists, is callable, has N parameters) or is a self-documenting case that
  already avoids text reads. Keep.

## Converted batch (files 1 and 2 — the densest, 31 assertions)

### `test_returning_clause.py` — 20 → 0

Two deletion-worthy groups were removed, one rewritten.

**Deleted — `TestP11SessionRefreshDocumentation` (3 assertions).**
`assert "session refresh" in TenantService.update_tenant.__doc__.lower()` tests
that a *comment* says a thing. It cannot fail on a behaviour regression and can
pass on a documentation regression. The docstrings remain accurate; the test
that policed them did not earn its place.

**Deleted — `TestP11PostUserServicesUnchanged` (2 assertions).**
`assert '"message": "Post updated"}' in source or "Post updated" in source` is
a duplicate of behavioural tests that already exist and pass:
- `tests/unit/modules/posts/test_service.py::TestPostServiceUpdate::test_update_post_success`
- `tests/unit/modules/users/test_user_service.py::TestUserServiceUpdate::test_update_own_profile`

Both verified passing before removal. Re-pinning the literal here added a third
place to update when the message copy changes.

**Rewritten — the refresh contract (15 assertions).**
The 12 substring assertions across the three mutation methods became a measured
call sequence. `TestP11SessionRefreshImplementation` now asserts one
`db.get` / one `commit` / one `refresh`, that `commit` precedes `refresh` in
`db.mock_calls`, and that the refreshed payload exposes the applied change.
`TestP11NoSecondReadThroughCrud` asserts `get_tenant` is never awaited and
`db.get.await_count == 1` — the re-introduced SELECT, stated so no spelling
evades it. Two new tests cover paths the source scan never checked: a commit
that fails must roll back, skip the refresh, and raise `DuplicateError`; a
missing tenant must raise before any write.

**Proof the rewrite bites.** The P1-1 defect was re-injected into
`update_tenant` (a second `get_tenant` read) and the converted tests failed —
`test_update_tenant_does_not_re_read` reported *"Expected get_tenant to not
have been awaited. Awaited 1 times."* Separately, removing only the
`db.refresh` failed `test_update_tenant_uses_session_refresh` with
`assert 0 == 1`. Production code was restored and byte-verified
(`self.db.get(Tenant` ×1, `await self.db.commit()` ×1,
`await self.db.refresh(tenant)` ×1, `await self.get_tenant(tenant_id)` ×0).

### `test_pool_behavior.py` — 11 → 0

The C2 invariant is an *ordering* of observable events, so the conversion
replaces text positions with a recorded event log. Each stubbed collaborator
appends to one `trace: list[str]`; `_TracedSession.commit` appends
`"db.commit"`. Assertions are then `trace.index("db.commit") <
trace.index("provider.invoke")`.

The strongest replacement checks the invariant **from inside the provider
call**: `_invoke` asserts `commit_count == 1` at the moment it is entered. No
text comparison can express that a connection was released before inference —
only execution order can.

- `test_complete_releases_the_connection_before_inference` — provider entered
  only after the release; response written after inference.
- `test_complete_stream_releases_the_connection_before_streaming` — same for
  the streaming path, asserted at the first chunk.
- `test_complete_writes_the_response_on_a_separate_session` — the write runs
  on a *different session object* than the released request session. The old
  `assert "fresh_db" in source` was satisfied by any variable name, including
  one holding the same released session.
- `test_persist_assistant_message_opens_and_commits_its_own_session` — the
  shared writer opens a session, writes, and commits that session, and never
  re-commits the request session.
- `test_persist_assistant_message_is_the_single_shared_write_path` — signature
  check, kept as a *shape* assertion (legitimate).

The genuine `asyncio.gather` speedup and concurrency-simulation tests were
kept untouched; they measure real elapsed time and are not source-text.

**Proof the rewrite bites.** Removing the single `await self.db.commit()` from
`_prepare_turn` — the C2 regression — failed 4 of the 6 release tests,
including both live in-provider checks. Restored and verified
(`await self.db.commit()` ×1 in `_prepare_turn`), 10/10 pass.

Both converted files: **22 passed, 0 failed**.

## Classification for the remaining 44

Not converted in this pass (deferred; recorded here so the next pass starts
from data, not from a grep).

| File | Assertions | Class | Notes |
|---|---|---|---|
| `perf_validation_r4.py` | 9 | rewrite | `:207` is **already failing** — see below. `:121` asserts `"flyweight" in source.lower()`, a comment. |
| `test_opus_optimizations_e2e.py` | 9 | delete + rewrite | `:145` asserts a *comment* string verbatim ("uvloop is deliberately not initialized here") — pure text. `:162` `"import orjson" in source` is import wiring. |
| `test_direct_dicts.py` | 6 | delete | All three tests duplicate what the converted `test_returning_clause.py::TestP11ResponseFormat` now asserts, better (isomorphism against `TenantResponse.model_dump()`). |
| `test_event_bus_parallel.py` | 5 | delete | Fully subsumed: the same file's `TestP14EventBusParallelEnqueue` already proves all 3 handlers enqueue, exception isolation, and the circuit-open sync fallback behaviourally. |
| `test_redis_pool.py` | 5 | rewrite | All three tests assert the same `"get_redis_pool" in source` — one rewritten test asserting the returned object shares the pool covers all three. |
| `test_cache_bytes.py` | 4 | delete + rewrite | `test_p23_comment_present` asserts comments. `test_no_decode_in_warm_tenant_cache` is rewriteable via a `FakeRedis.pipeline` double. |
| `test_ai_streaming_json.py` | 3 | delete | `tests/unit/modules/ai/test_ollama_provider.py` already drives `generate_text_stream` with a mocked stream; the stdlib/orjson equivalence tests in the same file are duplicates of `test_json_loads_matches_stdlib_behavior`. |
| `test_rollback_verification.py` | 3 | rewrite | Weakest of the set: `"rls" in source.lower() or "set_config" in source.lower()` passes on a bare import. The real contract is that the context sets RLS — assertable by entering the context with a fake session. |

## Findings that do not match the source of truth

- **`tests/e2e/performance/perf_validation_r4.py` has two pre-existing
  failures on a clean tree**, unrelated to this work:
  - `TestCRUDOperationsE2E::test_direct_dict_performance` — builds a
    `MagicMock` tenant with `id = 1` and a mock `feature_flags`, then calls
    `TenantResponse.model_validate`, which rejects both
    (`uuid_type`, `dict_type` errors). It has been failing against the current
    `TenantResponse`, not because of anything changed here.
  - `TestRateLimitingUnderLoadE2E::test_ip_parser_lru_cache_exists` — asserts
    `"lru_cache" in inspect.getsource(src.app.middleware.rate_limit)`. That
    module contains no `lru_cache`; the IP caches are in
    `src/app/core/utils/ip_utils.py`. This is a stale, already-failing text
    scan. **Left as-is** (not in the converted batch) but it is a live example
    of the failure mode: it fails on drift, not on behaviour.
- **`test_cache_warming.py:186-213` is the precedent.** Its
  `test_warm_caches_is_a_registered_startup_phase` docstring states that a
  source-text scan was replaced because `composition.py` "carried a comment
  telling engineers not to rename the symbol, because a test greps for it — a
  test-quality defect promoted into a production constraint." That is the
  exact pathology, already fixed in one place.
- **The stray `"lru_cache"` in `perf_validation_r4.py:214`** points at the
  wrong module entirely, which is the strongest argument for converting the
  remaining e2e batch next.

## Reproducing the count

The AST pass is reproducible and lives outside the trees (it is an audit tool,
not a test). Point it at the repo root:

```
python <counter>.py            # totals per file
python <counter>.py --detail   # every assertion, unparsed
```

It flags any test that reads `inspect.getsource` or `__doc__`, then attributes
each `assert` to the source-derived names in that test's scope, following
`.find()` offsets and `X or ""` propagation.

## Ratchet

`44` is the new floor for the two trees. Any new `getsource`/`__doc__`
assertion under `tests/performance/` or `tests/e2e/performance/` should be
refused in review: assert the behaviour at a seam instead. The converted files
document this in their own module docstrings, so the reasoning travels with the
code.
