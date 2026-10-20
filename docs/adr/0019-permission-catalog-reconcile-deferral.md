# ADR-0019: A-11 permission_catalog_reconcile deferral + fail-CLOSED question

**Status:** Decided — ACCEPT deferral off the critical path, with strict FAIL-CLOSED (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-4g4n` — "A-11 owner: name RBAC owner; deliver the permission_catalog_reconcile deferral + fail-CLOSED question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-11 defers the permission-catalog reconciliation and fails CLOSED meanwhile.
A closed failure denies legitimate access until the catalog catches up; only
the RBAC owner can accept that denial window. (Per the nn57 design, the
mandatory A-11 decision items ride on ADR-A8; this skeleton holds the
question, not the decision.)

## The question

> This is 977 ms of a 1 039 ms **cold** web-root boot (192 ms warm). A request arriving in the deferral window sees un-reconciled grants. Do you accept that window, and what is the fail direction — closed or open? Grant what?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-11**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT deferral off the critical path, with a strict FAIL-CLOSED
mandate.**

During the reconciliation window, any permission check for an
un-reconciled or stale grant must return `False` (deny). Do not serve
requests with missing grants. The fail direction is **CLOSED** — this
is not negotiable and is restated verbatim by the addendum (§5.2).

Magnitude note (addendum A-17): the 977 ms / 192 ms figures are
reclassified `[Unverified — Needs-Linux-measure]`; L-14 has since
measured the Linux web-root boot at 139.49 ms cold with
`permission_catalog_reconcile` at 112.77 ms. The security_signoff,
the named owner, the ADR, and the fail-CLOSED requirement are
unchanged — only the magnitudes moved. Because Linux boots faster,
deferral is less attractive there, and "do not bother" remains a
legitimate Phase-0 outcome.

## Amendment (2026-10-20): the Phase-0 outcome is DO NOT DEFER

Recorded against bead `FastAPI_saas-teh6` (the A-11 implementation
placeholder). The Decision above stands; what is recorded here is the
Phase-0 outcome and the reason for it. **Nothing in the decision, the
sign-off classification, the named owner, the ADR requirement or the
fail-CLOSED mandate is changed, weakened or re-opened by this amendment.**
The fail direction is still CLOSED and `MUST FAIL CLOSED` is still not
negotiable.

THE PLACEHOLDER'S PRECONDITIONS ARE MET.
- The RBAC owner is named in the header above and has accepted the
  deferral window in writing.
- This ADR states the fail direction as CLOSED.
- L-14's Linux breakdown exists (`FastAPI_saas-894r`, closed): the Linux
  web-root boot is 139.49 ms cold with `permission_catalog_reconcile` at
  112.77 ms, the ~6-10 DB round trips of PF-14 visible, taken on the Linux
  image with the per-phase boot breakdown and the `importtime` breakdown.

THE OUTCOME: DO NOT DEFER. The reason, in the programme's own terms.

1. A-11 does not serve the programme's goal. The placeholder says so
   itself: "SERVES THE GOAL: nothing on request p95 - this is BOOT time."
   It is a boot-time optimisation, not a fix for the 740 ms p95.
2. The Linux saving is 112.77 ms per boot — per deploy, per scale-up event
   — against a total cold boot of ~462 ms (web 139 ms and admin 323 ms,
   nested serially in one process). The original justification was 977 ms
   of a 1 039 ms cold boot. The magnitude note above already records that
   Linux boots faster, so deferral is less attractive there, and that
   "do not bother" remains a legitimate Phase-0 outcome. That note is
   read here as what it says.
3. The deferral is not free in kind, only in size. It moves the
   reconciliation off the boot path into a background task, which opens a
   window in which `migrate_successor_grants` has not yet carried the
   legacy `files:manage` grant forward to `files:write` / `files:delete`.
   The fail-CLOSED mandate covers that window by denying, but the denial
   is a real, user-visible access loss on every boot, and it is exactly the
   class of loss the run-3 finding that created this phase was about
   ("Silent, permanent permission loss").
4. It would change the failure semantics of the RBAC boot phase, in the
   most security-sensitive part of the application, in exchange for a
   boot-time number that is not on the goal's path.

The acceptance recorded on 2026-10-08 was made against the pre-
reclassification magnitudes and was never re-taken against the Linux
figures. `FastAPI_saas-qv7o` is where a re-confirmation would happen; this
closure does not treat the 2026-10-08 acceptance as sufficient on its own.

No test was weakened to accommodate this outcome, no assertion was
relaxed, and no threshold, ceiling or budget was moved.

