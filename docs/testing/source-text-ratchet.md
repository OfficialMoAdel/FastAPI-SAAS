# Source-text test ratchet (b1xu.4)

The count produced by `scripts/count_source_text_tests.py`. CI fails if the
count **increases**. Lowering this number is progress; raising it is the habit
returning, and will fail the build.

The ratchet is permissive of progress on purpose. A strict "zero source-text
tests" rule would have been disabled on day one — 114 existing assertions
cannot be deleted in one change. The number must go DOWN, not to zero.

## Baseline

    SOURCE_TEXT_TEST_BASELINE = 156

The baseline is enforced by `tests/core/test_source_text_ratchet.py`, which
imports `scripts/count_source_text_tests.py` and fails when the measured
count exceeds the constant above.

> **Note on the 115 → 116 step (ykto.4).** The 114 → 115 rise was already
> present on `main` when ykto.4 started and is not accounted for by any entry
> below; the baseline had simply not been re-measured. ykto.4's own addition
> is the 115 → 116 step, recorded here: `tests/unit/scripts/test_doc_gate_wiring.py`
> copies `scripts/checks/doc_paths.py` into a temporary tree and asserts on the
> subprocess **exit code** — the property under test is that the gate can fail,
> which cannot be observed any other way. The assertion is on behaviour (an
> observed returncode), but the copied `.py` path is what the counter's
> heuristic sees, so it is counted rather than exempted. Recorded so the
> increase is traceable to a file rather than looking like drift.

## Exemptions

A legitimate structural assertion — one where the property under test
genuinely IS the structure — is exempt and must be listed here with a reason.
An unexplained exemption list is habit 1 again: unenforced and undocumented
are the same thing.

    (none yet)

If you add an exemption, add the reason in the same commit. The ratchet test
fails on any exemption entry lacking one.

## What counts

A test counts when it reads a `.py` file's text (or `inspect.getsource`) and
asserts on it, WITHOUT also exercising behaviour. Reading a `.json` fixture or
a `.txt` asset is ordinary setup and does not count. A test that does both is
a behavioural test with an extra assertion, and is not the defect.

## Notes on the measured baseline

**2026-10-22: re-measured 156, and the ratchet is now enforced in the suite.**
The four Cluster regression files (`auth/test_cluster_a_regressions.py`,
`users/test_cluster_b_regressions.py`, `rbac/test_cluster_c_regressions.py`,
`notifications/test_cluster_d_regressions.py`) contributed 26 source-text
assertions. They were either deleted where a behavioural test already covered
the property, or converted to tests that execute the code (compiled
statements, mocked seams, mocked Alembic `op`). That removed all 26, leaving
156. `tests/core/test_source_text_ratchet.py` was added because this document
previously cited an enforcement test that did not exist; the baseline had
silently drifted from 116 to 182.

114 is measured, not asserted. The figure in the plan (44) counts FILES that
contain such an assertion; this counts individual test FUNCTIONS, which is the
finer-grained number a ratchet should track. Both are defensible; the
function count is the one that makes "did this PR add one?" visible in review.

**2026-09-29: raised 118 -> 119, deliberately.** The new count is
`tests/unit/scripts/test_verify_plan_metrics.py`, which reads `.py` source to
assert the metrics script is not silently returning zeros. That is a
LEGITIMATE structural assertion - the property under test IS the source
walker - but it is still a source-text test, so it is counted rather than
exempted. Recorded here so the increase is traceable to a specific file rather
than looking like drift.

**2026-09-30: raised 109 -> 114 (s92p.7).** Five additions, all in
`tests/contract/test_cross_module_seams.py`, which is itself a lint-gate test:
it asserts on source text because the property under test IS the text a lint
rule reads. That is the "legitimate structural assertion" case the exemption
section describes, so it is recorded here rather than exempted silently.

**2026-09-30: lowered 119 -> 109 (b1xu.1).** Ten assertions removed: five
duplicates of behavioural tests that already existed in
tests/unit/modules/posts and tests/unit/modules/users (verified passing there
before deletion), and five vacuous scans. The converted replacements assert
observable behaviour, verified by re-injecting the original defect and
watching them fail. See docs/SOURCE_TEXT_TEST_INVENTORY.md.

The mix is not uniform. Sampled entries include genuine defects (a regex over
every `router.py` looking for raw `Query(...)` pagination params — it passes
when the text matches, regardless of whether pagination works) and
legitimate structural checks (asserting a module defines no duplicate prefix
constant, where the absence of a constant IS the property). `b1xu.1` is the
inventory that classifies them; the baseline should drop as each is converted.
