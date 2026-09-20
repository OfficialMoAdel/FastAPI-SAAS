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
| RBAC role perms | L1 | 30s | `modules/rbac/service.py::_L1_RBAC_CACHE` | |
| Tier rate policies | L1 | 300s | `core/security/rate_limit_cache.py` | Path-map compiled alongside; invalidated per tier |
| Ban negative cache | L1 | 5s | `core/security/auto_ban.py::_BAN_NEGATIVE_TTL` | "not banned" memo; ban/unban evicts + publishes |
| Token blacklist warmup | L2 | (startup window) | `core/security/` warmup phase | Capped warmup, not a serving cache |
| Dashboard metrics | L2 | 60s | `modules/dashboard/service.py::METRICS_CACHE_TTL` | Per (tenant, range); fails open to recompute |
| Endpoint cache decorator | L2 | 3600s default | `core/utils/cache/decorator.py::expiration` | Per-decorator override (posts use 60s) |
| Feature flags (global) | L2 | `CACHE_TTL` | `modules/feature_flags/service.py` + warmer | Key `feature_flag:{key}` |
| Feature flags (tenant) | L2 | `CACHE_TTL` | `modules/feature_flags/service.py` | Keys via `build_tenant_key("features"/"feature:{name}")` |
| Webhook endpoint cache | L2 | (service-defined) | `modules/webhooks/service.py` | INTERNAL only — serialized rows carry secrets for the signing worker |
| Query profiler | n/a | request-scoped | `core/db/profiler.py` | Dev-only, default OFF (P2) |

Rules of thumb:

- L1 TTLs must be SHORTER than their L2 counterpart (stale window =
  L1 TTL, bounded by pub/sub fan-out).
- Any new L1 cache MUST self-register its `InvalidationChannel` with
  `core/cache/l1_registry.py` (I11) and be added to
  `import_l1_owner_modules()`.
- Document a new TTL here in the same PR that introduces it.
