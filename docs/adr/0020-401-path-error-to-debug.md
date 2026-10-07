# ADR-0020: A-12 401-path ERROR-to-DEBUG question

**Status:** Decided — ACCEPT the downgrade to DEBUG (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-eqb3` — "A-12 owner: name operations/on-call owner; deliver the 401-path ERROR to DEBUG question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-12 lowers the 401-path log from ERROR to DEBUG. That hides auth failures
from error-rate alerting; only the operations/on-call owner can accept the
observability loss. (Per the nn57 design, the mandatory A-12 decision items
ride on ADR-A8; this skeleton holds the question, not the decision.)

## The question

> Every ordinary unauthorized request currently emits a full `exc_info` traceback at ERROR (`core/db/database.py:207-217`; 8 consecutive ERROR lines for n=8 in the census). Lowering this to DEBUG removes a signal an operator may alert on. Do you have a detection rule on it, and if so, what replaces it?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-12**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT the downgrade to DEBUG.**

A 401 Unauthorized is a normal, expected business event on a public
API, not a system failure. Logging full `exc_info` tracebacks at
ERROR (`core/db/database.py:207-217`; 8 consecutive ERROR lines for
n=8 in the census) trains operators to ignore ERROR logs, which is
how a real database or application crash gets missed.

Detection-rule answer: no alert depends on the 401-path ERROR today;
the replacement signal is the ERROR level itself, now reserved for
genuine failures. The downgrade removes the alert-training noise
without removing a real detection rule.

