# ADR-0027: Phase 5 — the ADR-0015 idempotency guard, and two deliberate non-changes

**Status:** ACCEPTED — 2026-10-23
**Date:** 2026-10-23
**Tracked by bead:** (to be filed — records Phase 5 of `REPORT 1-PLAN FOR ADVANCED OPTIMIZATIONS.md`)
**Owner:** OfficialMoAdel <mohamed01023618534@gmail.com>
**Measured by:** `tests/unit/core/security/test_phase5_guard_and_policy.py`

## The questions

1. **§8.3** — the merged GCRA composites fall back to the sequential helpers on
   ANY exception. ADR-0015 rejected that shape because a timeout that fired
   *after* the server executed the script makes the fallback consume the same
   budget a second time. What guard satisfies the ADR?
2. **F-14** — `restore_rls_context` warns on every tenant-less checkout. The
   report recorded two positions (fix it / it is not isomorphic) and left the
   choice open.
3. **§6** — which of the micro-sweep items are real, and which are load-bearing
   behaviour that must NOT be hoisted?

## Decision

### §8.3 — ACCEPT: a nonce claimed inside the script

The composite's **first** operation is now `SET nonce 1 NX PX <ttl>`. A second
execution for the same request returns `{'duplicate'}` and consumes nothing.

The nonce is in the script because the ambiguity it resolves — *did the server
execute it?* — is a property of the server. A marker set in Python before the
call cannot distinguish "never ran" from "ran, reply lost"; a marker set after
cannot be reached when the call raised. Claiming it first is the only point
that makes the two cases distinguishable.

The fallback then has exactly two behaviours:

| Evidence | Meaning | Action |
|---|---|---|
| nonce present | the script **did** run; the budget is already spent | **fail closed (429)** — ADR-0015's sanctioned direction |
| nonce absent | the script **provably** never ran | sequential path, one advance |

This is the ADR's "strict idempotency guard" branch, not the rejected naive
fallback. `test_timed_out_merged_call_consumes_at_most_once` (previously
`xfail(strict=True)` in `tests/performance/test_rate_limit_behavior.py`) is the
contract this closes.

**One deliberate narrowness:** the probe accepts only a *genuine* `int` result
(`type(result) is int and result == 1`). A mock, or any object with `__bool__`,
is truthy by default, which would make every script error look like "already
executed" and fail closed on a fault that never ran anything.

### F-14 — ACCEPT, as an explicit policy decision

The no-tenant record is now `logger.debug`. The reset itself is unchanged and
still unconditional — it is the fail-closed behaviour; only the log line's
level changed. The genuine failure path still logs `CRITICAL` and raises.

This is **not** claimed to be isomorphic: it changes observable log output.
It is recorded as a policy call, which is what the report said it was.

### §6 — ACCEPT the hoists, REJECT one

Hoisted: `uses_gcra()`'s constant comparison (now `_USES_GCRA`, computed at
import), `math` and `build_tenant_key` to module scope in `rate_policy`, and
the per-header scan location to `_HEADER_LOCATIONS` (was an f-string +
latin-1 decode per scanned header, ~6/request).

**REJECTED: hoisting the four attack-detection settings out of `__call__`.**
The report proposed reading them once at construction. They are re-read per
request *on purpose* — `ATTACK_DETECTION_MODE`, `_ENABLED`, `_LOG_FULL_REQUEST`
and `_BRANCH_PRUNE` are incident kill switches that an operator flips without a
deploy. Hoisting them would silently convert a live switch into a restart-only
one, which is a security regression disguised as a micro-optimisation.
`test_the_per_request_settings_read_is_deliberate` pins this.

## Re-open condition

Re-open the guard if a request can legitimately reach the composite twice with
the same nonce (it cannot: the nonce is minted per call), or if the nonce TTL
(30 s) is ever shorter than a request's own retry budget.
