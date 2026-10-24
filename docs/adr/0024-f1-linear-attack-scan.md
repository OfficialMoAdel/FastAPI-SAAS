# ADR-0024: F-1 — the four super-linear attack-scan branches are read by a linear scanner

**Status:** ACCEPTED — 2026-10-23
**Date:** 2026-10-23
**Tracked by bead:** (to be filed — this ADR is the record that closes F-1 of `REPORT 1-PLAN FOR ADVANCED OPTIMIZATIONS.md` Phase 1)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Measured by:** `scripts/perf-validation/isomorphism_validator.py` (`F1_scan`), `tests/unit/middleware/test_attack_scan_linear_differential.py`

## The question

Four branches of `COMBINED_ATTACK_RE` (`sql_union`, `sql_drop`, `sql_delete`,
`sql_exec`) are of the form `\b<open>\b .*? \b<close>\b` with **no
`re.DOTALL`**. Each start position rescans to end-of-line, so a single-line
body of `b"union " * k` with no `select` costs **O(n²)** — measured ~1.33 s at
32 KB and ~14 s at 96 KB. The scanner runs **before routing, on the event
loop, for every method**, on the query string and the headers (nginx default
8 KB each) and on bodies up to `_MAX_SCAN_SIZE = 128 KB`; the offload does not
help because CPython's `re` holds the GIL while matching. Is the branch's
language — "an *open* token, then a *close* token later on the **same line**"
— reproducible by a single left-to-right pass whose merged output is
byte-identical to `COMBINED_ATTACK_RE.finditer`?

## Decision

**ACCEPT.** The four branches are read by the linear token scanner in
`src/app/middleware/attack_scan_linear.py`; the remaining thirteen (already
linear) stay in `re`, compiled from `COMBINED_ATTACK_RE.pattern` with the four
**removed by name** so the pattern cannot drift.

Five rules, each mechanical:

1. **No new dependency.** The token search runs on the raw bytes with
   `re.IGNORECASE` (ASCII-only fold) — the same fold `re.I` applies to the
   bytes pattern today. This is why the bytes-capable Aho–Corasick engine was
   **not** taken: it is a new native dependency inside a security control, and
   it is a separate decision (see "Not in this ADR").
2. **No detection change.** All 17 branches are kept. The merged match
   sequence is asserted byte-identical — `(lastgroup, start, end)` — to the
   original single-pattern `finditer`, including the non-overlapping match
   **count and order** and the preemption case
   (`"union <script>x</script> a select"` yields `sql_union` only, because the
   cursor swallows the `<script>`, exactly as the regex does).
3. **The cursor is the whole point.** The thirteen-branch side is queried
   `search(content, cursor)`, NOT a precomputed non-overlapping stream: when a
   four-branch match ends *inside* an earlier thirteen-branch match, a
   precomputed stream has already let that earlier match suppress the later
   one the cursor needs. The differential corpus caught this (see
   `TestTheCursorIsTheWholePoint`).
4. **A kill switch.** `ATTACK_DETECTION_LINEAR_SCAN` (default `True`) restores
   the original whole-pattern scan — the pre-fix, super-linear behaviour — so
   an operator who suspects the scanner has an immediate, no-deploy fallback.
   The switch is a **capacity/incident** lever, never a detection lever: the
   linear path is isomorphic and never misses.
5. **The gate can fail.** `F1_scan` imports the real modules and asserts
   equality over the 17 per-group probes, a seeded token-soup fuzz (20 000
   cases), the quadratic trigger classes and the cursor regression; the
   validator now **exits non-zero** when any gate fails, so the CI step fails
   instead of printing `FAILED` and succeeding.

## Not in this ADR

- **F-5 copy removal in the prefilter.** `_attack_prefilter_hit` still makes
  two full-buffer copies (`.lower()` + `.decode("latin-1")`) because
  `pyahocorasick`'s C automaton accepts `str` only (verified: `add_word(b"...")`
  → *string expected*). Removing them needs a **bytes-capable** engine — a new
  dependency — and is therefore a separate decision, not a Phase-1 change.
- **The bounded-gap interim.** ADR-0010 already ACCEPTED the 512-char gap
  ceiling (`[^\n]{0,512}?`; **not** `[\s\S]{0,200}?`, which crosses newlines and
  is not semantics-preserving). It is available as a labelled interim if the
  linear scanner must be disabled, and is ~7× on the recorded class — an
  interim, not the fix.

## Re-open condition

Re-open if (a) the `F1_scan` gate fails, (b) an 18th branch is added (the gate
asserts the branch count and the four-branch set), or (c) a future change makes
a four-branch token ambiguous across the newline rule.
