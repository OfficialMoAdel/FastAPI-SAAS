# ADR-0013: A-5 API-key MGET fail-direction question

**Status:** Decided — FAIL-CLOSED (propagate) (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-w9w3` — "A-5 owner: name auth owner; deliver the API-key MGET fail-direction question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-5 batches API-key cache reads into one MGET. The fail direction (open vs
closed) when the store is down is a policy call only the auth owner can make.

## The question

> Today a Redis outage **raises** out of `RedisCacheAdapter.get` (`core/cache/client.py:89-95`, no try/except); fall-through-to-DB happens on a cache **miss**, not an outage. A swallowing `mget` changes the answer to 'Redis is down, do we authenticate?' from *propagate* to *fail-soft-to-DB*. Pick the semantics deliberately and pin them in a test.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-5**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**FAIL-CLOSED (propagate). The MGET must not swallow connection
errors.**

A Redis outage must not trigger a flood of API-key validation queries
to the primary database, which would cause a cascading failure.
`RedisCacheAdapter.get` (`core/cache/client.py:89-95`, no try/except)
keeps its propagate-on-outage semantics: a swallowing `mget` is
rejected because it changes the answer to "Redis is down, do we
authenticate?" from *propagate* to *fail-soft-to-DB*.

Consequence, stated plainly: during a Redis outage, API-key
authenticated routes return **503 Service Unavailable** rather than
falling through to the database. The fail direction is named here
explicitly, as §14 Gate D requires for A-5.

Test requirement: pin the semantics — a connection error during the
MGET must raise, not fall through to the DB.

