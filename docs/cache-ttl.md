# Cache TTL Registry

Single reference table for every cache TTL in the codebase (P2 "TTL
sprawl" item — documentation only, no code change). Keep this table in
sync when touching a TTL. L1 = in-process TTLCache; L2 = Redis.

| Cache | Layer | TTL | Owned by | Notes |
|---|---|---|---|---|
| Tenant resolution | L1 | 60s | `core/tenancy/cache.py::_L1_TENANT_CACHE` | Deliberately distinct from L2 — do not unify (bounded stale-`is_active` window) |
| Tenant resolution | L2 | 300s | `core/tenancy/cache.py::TENANT_CACHE_TTL` | Negative caching included; cross-worker invalidation via pub/sub |
| User profile | L2 | 300s | `modules/users/cache.py::USER_CACHE_TTL` | Keyed by id/email/username |
| User auth-info | L1 | 8s | `modules/users/cache.py::_AUTH_INFO_L1_TTL` | Short by design — revocation is synchronous via Redis DELETE |
| User auth-info | L2 | 300s | `modules/users/cache.py::AUTH_INFO_CACHE_TTL` | |
| RBAC bitmask | L1 | 30s | `core/rbac/dependencies.py::_BITMASK_L1_TTL` | Cross-process gap bounded by pub/sub fan-out |
| RBAC bitmask | L2 | 300s | `core/rbac/dependencies.py::PERMISSIONS_CACHE_TTL` | Version-hashed keys |
| RBAC role perms | L1 | 30s | `modules/rbac/service.py::_L1_RBAC_CACHE` | Process-local; other workers may serve stale grants/revocations for up to 30s (no pub/sub) |
| Tier rate policies | L1 | 300s | `core/security/rate_limit_cache.py` | Path-map compiled alongside; invalidated per tier |
| Ban negative cache | L1 | 5s | `core/security/auto_ban.py::_BAN_NEGATIVE_TTL` | "not banned" memo; ban/unban evicts + publishes |
| Token blacklist warmup | L2 | (startup window) | `core/security/` warmup phase | Capped warmup, not a serving cache |
| Dashboard metrics | L2 | 60s | `modules/dashboard/service.py::METRICS_CACHE_TTL` | Per (tenant, range); fails open to recompute |
| Endpoint cache decorator | L2 | 3600s default | `core/utils/cache/decorator.py::expiration` | Per-decorator override (posts use 60s) |
| Feature flags (global) | L2 | `CACHE_TTL` | `modules/feature_flags/service.py` + warmer | Key `feature_flag:{key}` |
| Feature flags (tenant) | L2 | `CACHE_TTL` | `modules/feature_flags/service.py` | Keys via `build_tenant_key("features"/"feature:{name}")` |
| Webhook endpoint cache | L2 | (service-defined) | `modules/webhooks/service.py` | INTERNAL only — serialized rows carry secrets for the signing worker |
| Query profiler | n/a | request-scoped | `middleware/query_profiler.py` | Dev-only, default OFF (P2); single implementation |

Rules of thumb:

- When an L1 cache has an L2 counterpart, its TTL must be SHORTER than the
  L2 TTL (stale window = L1 TTL, bounded by pub/sub fan-out).
- L1-only caches have no L2 fallback. Cross-worker staleness is bounded by
  the L1 TTL unless they also participate in pub/sub; document that window
  in the Notes column.
- Any new L1 cache MUST self-register its `InvalidationChannel` with
  `core/cache/l1.py::register_l1_channel` (I11) and its owner module must be
  added to `import_l1_owner_modules()` in `src/app/composition.py`, which is
  the one enumeration the boot depends on.
- Document a new TTL here in the same PR that introduces it.

## Cache index TTLs

The Redis invalidation indexes are metadata ABOUT data keys, so their TTL is
derived, never chosen independently: every index writer passes its data TTL
through `core/utils/cache/index_policy.py::index_ttl` (2× the data TTL). An
index that expires at or before its data key lets a later write find an empty
index and miss still-live keys (stale reads); a dead index member is harmless
— deleting an expired key is a no-op.

There are TWO index families and they are deliberately not merged:

| Family | Keys | Written by | Drained by |
|---|---|---|---|
| Pattern index | `idx_pattern:{pattern}` (set of member keys) | `register_key_patterns`, `set_cache_with_patterns` | `invalidate_by_pattern` (`cache_pattern_drain` Lua) |
| Tenant index | `tenant_index:{tid}` (global) + `tenant_index:{tid}:{prefix}` (shards) | `set_tenant_cache`, `add_to_tenant_index`, the endpoint cache decorator | `invalidate_tenant_cache` (`cache_tenant_prefix_drain` / `cache_tenant_global_drain` Lua) |

Drain = one atomic server-side Lua script (registered in
`core/utils/lua_manager.py::LuaScriptManager.BUILTIN_SCRIPTS`) that reads the
index, deletes the members, cleans the index itself, and returns the deleted
count — never a Python SMEMBERS-then-pipeline sequence, which races with
writers between the read and the delete. The tenant prefix drain keeps the
S-14 guarantee: an empty shard falls back to the global index and filters by
colon-boundary prefix match before giving up.
