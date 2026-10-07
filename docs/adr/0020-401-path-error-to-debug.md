# ADR-0020: A-12 401-path ERROR-to-DEBUG question

**Status:** Reported — not fixed
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-eqb3` — "A-12 owner: name operations/on-call owner; deliver the 401-path ERROR to DEBUG question"
**Owner:** _Unassigned — no Owner field exists anywhere in the programme; its absence is Q-11._

## Context

A-12 lowers the 401-path log from ERROR to DEBUG. That hides auth failures
from error-rate alerting; only the operations/on-call owner can accept the
observability loss. (Per the nn57 design, the mandatory A-12 decision items
ride on ADR-A8; this skeleton holds the question, not the decision.)

## The question (UNANSWERED)

> Every ordinary unauthorized request currently emits a full `exc_info` traceback at ERROR (`core/db/database.py:207-217`; 8 consecutive ERROR lines for n=8 in the census). Lowering this to DEBUG removes a signal an operator may alert on. Do you have a detection rule on it, and if so, what replaces it?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-12**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

None — this ADR is an unanswered skeleton. Answering it here, or pre-answering
it before the named owner responds, would be a Gate D violation.
