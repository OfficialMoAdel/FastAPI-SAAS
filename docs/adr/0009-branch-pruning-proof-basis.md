# ADR-0009: A-1 branch-pruning proof basis

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-v6vs` — "A-1 owner: name threat-model owner + security reviewer; deliver the branch-pruning proof-basis question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-1 proposes pruning branches of the attack-scan combined regex per payload.
The prune is only safe with a proof basis the security reviewer accepts.

## The question (UNANSWERED)

> A branch is removed from the compiled alternation only when its necessary-literal conjunction is absent. The per-branch CNF (17 rows, `attack_detection.py:85-102`) is machine-verified as a *necessary* condition against the source. Do you accept (i) a **necessary-condition-only** proof as the soundness basis, (ii) a differential oracle covering the `(lastgroup, start, end)` tuple on an **ASCII + NFKC + zero-width** corpus, with the metamorphic property **P1 — for every branch `b` and every known payload `p` for `b`, `b ∈ select(literals_of(p))`** as the only test that can catch an over-strengthened literal, and (iii) that a single wrong literal produces a **missed detection** that no benchmark will surface?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-1**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.

## Routing: what the named owner has to do with this bead

Bead `FastAPI_saas-v6vs` may **not** be closed by an engineer. Naming the
threat-model owner and the security reviewer is a project-lead action, and the
repository contains no `CODEOWNERS` file and no roster of named humans, so the
two names cannot be derived here. Two header fields below are therefore still
empty on purpose:

- **`Owner:`** (line 6) — needs one named individual for **A-1**.
- **`Security reviewer:`** — absent; needs one named individual, and it is a
  different person from the threat-model owner. A-1 is a *detection-soundness*
  claim, so the reviewer has to be someone who can accept or reject a missed
  detection, not the author of the optimisation.

The bead closes when **both** are named here and the question above is answered
in writing in this file. Until then it stays open, and no branch of
`COMBINED_ATTACK_RE` may be pruned.

### How to deliver the answer

Paste the answer into this file under a new `## The answer` heading, directly
after the question. It has to be a decision, not an acknowledgement: name which
of (i)/(ii)/(iii) are accepted, and for each one not accepted, say what would
make it acceptable. Record the answer in the bead with
`bd update FastAPI_saas-v6vs --notes "..."` as well, because `bd show` is what
the gate register reads.

### What a complete answer must settle

The three clauses are one question, not three, and an answer that takes (i)
without also settling (ii) has not answered it: (i) is the soundness argument
and (ii) is the only thing that can falsify it.

### Two facts about the current code that bear on the answer

Recorded because the owner cannot see them from the plan text, and because one
of them contradicts the question's own premise. Neither pre-answers it.

1. **The premise "machine-verified as a *necessary* condition against the source"
   does not hold today.** A repo-wide search for the P1 property
   (`select(literals_of(p))`, `lastgroup` differential oracles, `NFKC` corpus
   fixtures) finds no test that verifies the necessary-condition claim, and no
   harness that emits a per-branch CNF. What exists is the *claim* as a comment,
   not the verification.
2. **The per-branch CNF is 17 groups over 15 literals, and three of the rows are
   disjunctions, not single literals.** The mapping is written out in prose at
   `src/app/middleware/attack_detection.py:113-118`; the literals live in
   `_PREFILTER_LITERALS_BYTES` at `attack_detection.py:119-137`
   (`attack_detection.py:83-102` is the regex itself, the 17 alternation
   branches). `sql_comment`/`sql_semicomment` share `"--"`, the four `xss_*`
   tag/event branches share `"<"`, and **`cmd_inj` is a three-way disjunction**
   (`;` OR `||` OR `&&`). So "absent the branch's necessary literal" is, for
   `cmd_inj`, absent all three — a different and more expensive predicate than
   for the other sixteen rows. Any pruning plan that treats every row as
   single-literal has mis-stated the property for at least one branch.
