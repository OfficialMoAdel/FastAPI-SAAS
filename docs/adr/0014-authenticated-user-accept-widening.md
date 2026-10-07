# ADR-0014: A-6 AuthenticatedUser accept-widening question

**Status:** Decided — REJECT the accept-widening (2026-10-08)
**Date:** 2026-10-06
**Tracked by bead:** `FastAPI_saas-ydan` — "A-6 owner: name auth owner; deliver the AuthenticatedUser accept-widening question"
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com> (recorded 2026-10-08)

## Context

A-6 widens what `AuthenticatedUser` accepts. The auth owner must accept the
widened contract before untrusted shapes can flow through it.

## The question

> `UserAuthInfo` requires `hashed_password`; `AuthenticatedUser` uses `extra='ignore'` and does not. Skipping `model_validate` therefore **widens the accept path**: any cache-write path that omits the field — or a poisoned Redis entry — becomes authenticatable. Is that widening acceptable? Also: `tenant_id` changes type (`UUID`→`str`) and `tier` contents (`datetime`→ISO string); the risk is a downstream `==` against a `UUID`.

Verbatim from `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_1_UNIFIED.md` §9 (the row for **A-6**, column 4). An earlier
revision of this file claimed the verbatim question was not retrievable; it is,
and transcribing it here by hand is exactly the paraphrase this file exists to
prevent, so it is carried as the plan's own bytes.

## Decision

**REJECT. Keep `UserAuthInfo.model_validate()`.**

The performance benefit (~28 µs/req, perf G9) is below the noise
floor and immaterial. Widening the accept path — so that any
cache-write path that omits `hashed_password`, or a poisoned Redis
entry, becomes authenticatable — is an unacceptable security
regression for zero measurable gain. `AuthenticatedUser` keeps
`model_validate` and its `extra='ignore'` contract; the `tenant_id`
(UUID→str) and `tier` (datetime→ISO string) type changes are not
adopted on this basis either, so no downstream `==` against a `UUID`
is introduced.

This is the one Lane D item that is a flat rejection: no
implementation of A-6 may widen the accept path.

