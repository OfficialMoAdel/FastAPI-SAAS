# Cross-module import counts: UNRESOLVED

`fxh1.3` and `fxh1.6` produced three different answers for the same metric.
This note records that, rather than picking a fourth.

## The three answers

| Method | facade (sanctioned) | other internals |
|---|---|---|
| `fxh1.3` agent | 125 | 155 |
| `scripts/verify_plan_metrics.py` | 545 | 93 |
| My independent classifier (2026-09-29) | 320 | 614 |

All three total 638-639 import statements naming `src.app.modules.*`, so they
agree on the DENOMINATOR and disagree on the SPLIT. The disagreement is
entirely in classification.

## Why they differ

The phrase "another module's internals" has at least three defensible readings,
and each method implemented a different one:

1. **Statement-level vs target-level.** `from a.models import User` names two
   modules (`a.models` and `a.models.User`) but is one statement. Counting
   targets roughly doubles the number; counting statements does not.
2. **What counts as "the file's own module".** `modules/ai/models.py` importing
   `modules/_base/schemas` is one method's internals to a reader and a shared
   library to another. `modules/_base` is documented as shared, not a feature.
3. **Whether a `modules/*/__init__.py` facade is sanctioned for ALL its imports**
   or only for the re-export form. The `fxh1.3` count (125) captured only the
   shallow `from src.app.modules.X import Y` form and missed the facade-file
   re-export form; the script (545) counts everything inside a facade.

## What is NOT in doubt

- 638 import statements name `src.app.modules.*`. (Established, three ways.)
- TID251 is clean: 0 violations. (Established, and proven causally by removing
  the facade exemption on a scratch copy and observing 13 errors appear.)
- The split is contested, so the PLAN ROW 3 does not quote a per-category
  number. The fxh1.3 edit was corrected on 2026-09-29.

## Consequence for the plan

`fxh1.3` reworded row 3 to distinguish "routed through a sanctioned facade"
from "gate violations". That reframing is correct and stands. The 125/155 split
it quoted is **withdrawn as unreliable**, not replaced — the script's 545/93
split is not obviously better, only differently wrong. Both are replaced by the
one fact that IS solid: deep cross-module imports are real and numerous, the
gate is clean by design, and the precise breakdown needs a definition agreed
before it is published.

This is the exact failure `fxh1.6` exists to prevent: one quantity, four
numbers, because nobody defined the metric.
