# ADR-0004: Per-process L1 caches accept bounded eventual consistency

**Status:** Accepted (2026-08-29) — implements P1-6 of `PLAN_FOR_ADVANCED_OPTIMIZATIONS_ROUND_0.md`
**Decider:** Platform team

## Context

Several hot-path caches live in-process (`TTLCache` per Python worker): tenant metadata (60s, with Redis pub/sub invalidation), RBAC permission sets (30s, **no** pub/sub), and the auto-ban negative cache (5s, **no** pub/sub). The `auto-ban` and `token-blacklist-cache` modules had earlier been the target of three-plan-rotation optimizations and converged on the explicit acknowledgment that "the cache is per-process so a ban set by another worker is not seen until expiry" (comment at `src/app/core/security/auto_ban.py:30-39`).

With multi-worker deployments (`gunicorn --workers 4`, plus ARQ workers, plus cron processes) writes to security state from one worker were visible to others only after the TTL expired:

- **Ban** (5s window): a banned IP could send up to ~5s of requests through the worker that didn't issue the ban. For a rate-limit defense, acceptable; for a primary security boundary, not.
- **RBAC** (30s window): a role grant or revoke could be ignored for up to 30s by permission checks on other workers. P1-5 of the Round 0 plan flagged this as a multi-worker revocation lag.
- **Token-blacklist** and **tenant metadata**: already had pub/sub fan-out (or were DB-authoritative with a bounded 60s fallback race in the worst case).

The trade-off the previous plans offered: keep the in-process L1 (saves 0.5ms on the hot path) vs. drop it (extra Redis round-trip on every check). The cost was that revocation could be invisible to up to 75% of the fleet for the full TTL.

## Decision

Two changes, matching the plan's option set:

1. **Auto-ban** gains a Redis pub/sub invalidation channel (`ban:l1_invalidate`, mirroring `tenant:l1_invalidate` at `src/app/middleware/tenant.py:45-72`). `ban_ip` / `unban_ip` publish the affected IP; every worker subscribes once at boot via `_start_ban_l1_invalidation_listener` (defined in `src/app/core/security/auto_ban.py:41-92`). Same-process evictions already existed (`_ban_negative_cache.pop`); cross-worker evictions are now sub-second.

2. **RBAC** keeps the in-process L1 (it's a much hotter path than ban — every permission check reads it) and KEEPS its 30s TTL. The code at `src/app/core/rbac/dependencies.py:55-60` records why 30s is the right bound rather than a smaller one: the synchronous happy path on a role change is a Redis DELETE, so the L1 TTL bounds only the cross-process pub/sub gap, not the 300s Redis TTL. Adding a pub/sub channel for RBAC would double the invalidation traffic (role changes are more frequent than bans) without a comparable security win. *(An earlier revision of this ADR said the TTL drops 30s→5s; the code never changed to 5s, and the reasoning above is why it should not. The claim is corrected here rather than left to be re-copied.)*

## Consequences

- **Ban slip window**: 5s → sub-second (limited by Redis pub/sub delivery latency, typically <10ms).
- **RBAC revocation lag**: bounded by `_BITMASK_L1_TTL = 30.0` for the cross-process gap (the same-process delete is synchronous). Still non-zero; **do not rely on instant propagation** for user-visible "your role was changed" flows. Tests that assume immediate visibility must call `invalidate_user_permissions_cache` explicitly (already required by `rbac/service.py:199`).
- **Per-worker memory**: unchanged. The L1 caches are bounded (`maxsize=500` for RBAC, `10000` for ban).
- **Redis load**: a small constant increase (publishes on every ban/unban). RBAC is unaffected.
- **Failure mode**: if Redis is unavailable, `_publish_*` logs a warning and continues — local evictions still apply, so single-process correctness is preserved. The L1 just becomes eventually consistent via TTL (the previous behavior).

## Alternatives considered

- **Drop the L1 entirely.** Two extra Redis RTTs per permission check, per ban lookup. Quantified at 0.5ms/p99 on the previous R2 plan. Rejected: hot-path regression, security gain is small once the ban path has pub/sub.
- **Add pub/sub for RBAC too.** Doubled invalidation traffic and a fan-out on every `RoleService.delete_role` / grant / revoke. The 30s TTL bound is acceptable for RBAC; not worth the infrastructure cost.
- **Use Redis Streams instead of pub/sub.** Better delivery guarantees but adds a consumer-group and a separate Redis primitive. The current pub/sub model matches the existing tenant pattern and keeps the dependency surface flat.

## Verification

- Two-process test (or two-task test in a single process with `asyncio.create_task`): a ban set by worker A is visible to worker B on the next lookup.
- Lint and mypy clean on modified files: `src/app/core/security/auto_ban.py`, `src/app/modules/rbac/service.py`, `src/app/main.py`.
- `tests/unit/core/security/test_auto_ban.py` still passes.
