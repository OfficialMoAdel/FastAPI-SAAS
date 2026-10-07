# ADR-0019: A-11 permission_catalog_reconcile deferral + fail-CLOSED question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-4g4n` — "A-11 owner: name RBAC owner; deliver the permission_catalog_reconcile deferral + fail-CLOSED question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-11 defers the permission-catalog reconciliation and fails CLOSED meanwhile.
A closed failure denies legitimate access until the catalog catches up; only
the RBAC owner can accept that denial window. (Per the nn57 design, the
mandatory A-11 decision items ride on ADR-A8; this skeleton holds the
question, not the decision.)

## The question (UNANSWERED)

> This is 977 ms of a 1 039 ms **cold** web-root boot (192 ms warm). A request arriving in the deferral window sees un-reconciled grants. Do you accept that window, and what is the fail direction — closed or open? Grant what?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-11**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
