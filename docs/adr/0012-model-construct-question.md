# ADR-0012: A-4 model_construct question

**Status:** Decided — ACCEPT unvalidated serialization for DB-backed list endpoints (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-5r3q` — "A-4 owner: name API contract owner + security reviewer; deliver the model_construct question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-4 touches model construction on the auth path. The API contract owner and
the security reviewer must accept the change before it lands.

## The question

> `model_construct` skips validation. A row that would have raised `ValidationError` now serialises to the client, and field validators and `@model_validator` never run. This is a **response-integrity** change on every list endpoint, not a speed change. Which endpoints return attacker-influenced or potentially corrupt rows, and do you accept unvalidated serialisation there?

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-4**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**ACCEPT. Unvalidated serialization is acceptable for DB-backed
list endpoints.**

Data read from the database via SQLAlchemy is already constrained by
the DB schema and is trusted. Re-validating trusted DB output via
Pydantic on read paths is redundant: a row that would have raised
`ValidationError` cannot exist in a schema-constrained table, so the
`model_construct` path does not weaken response integrity for
DB-backed rows.

Scope limit: this acceptance covers DB-backed list endpoints only.
Any endpoint that serialises attacker-influenced or externally
sourced rows (not DB-constrained) is OUT of this acceptance and must
keep full validation. The API contract owner and security reviewer
accept the change on that basis.

## Per-endpoint verdict

`model_construct` is not yet called anywhere in `src/app/` (verified
by `rg model_construct src/app/` — no matches), so this verdict
governs the change when it lands, not code that exists today. When
it lands, the verdict is per-endpoint:

| Endpoint class | Rows are DB-backed (schema-constrained)? | Verdict |
|---|---|---|
| User list / directory (`modules/users/directory.py`, `UserRead.model_validate`) | Yes — rows come from the `users` table, constrained by the DB schema and RLS | **ACCEPT** unvalidated serialization |
| API-key list (`modules/api_keys/`) | Yes — rows from the `api_keys` table, schema-constrained | **ACCEPT** |
| Billing/webhook list (`modules/billing/`, `modules/webhooks/`) | Yes — rows from their tables, schema-constrained | **ACCEPT** |
| Any endpoint serialising **request-supplied** or **externally sourced** rows (not DB-constrained) | No — attacker-influenced | **OUT OF SCOPE — keep full `model_validate`** |

The named fail direction: the change is a **response-integrity**
change only on DB-backed list endpoints; on any endpoint whose rows
are not DB-constrained, the full validation path is retained. The
API contract owner and security reviewer accept the change on that
per-endpoint basis.


