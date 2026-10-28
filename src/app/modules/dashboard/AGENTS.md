# Dashboard Module AGENTS.md

## Package Identity
Read-only aggregation facade for dashboard metrics (users, revenue, activity).
Owns no database tables — aggregates other modules' models in a single
snapshot query. Follows the read-only module pattern (no `models.py`).

## Setup & Run
```bash
# Type check
uv run mypy src/app/modules/dashboard/

# Lint
uv run ruff check src/app/modules/dashboard/
```

## Patterns & Conventions

### Code Organization
- ? DO: Keep this module read-only — no writes, no owned tables
- ? DO: Scope every subquery by `tenant_id` (tenant isolation is structural here)
- ? DO: Use the sanctioned read-only seam: `audit` / `billing` / `users` models
- ? DON'T: Import other modules' services — aggregate their models directly

### Aggregation Pattern
```python
# Example from src/app/modules/dashboard/service.py:

async def _get_all_metrics(self, db, start, end_date, tenant_id, ...):
    """Fetch all 11 aggregates in ONE statement (one snapshot, one RTT)."""
    audit = self._audit_grouped_subquery(start, end_date, tenant_id)
    columns = [
        *self._user_subqueries(start, end_date, tenant_id, audit=audit),
        *self._revenue_subqueries(start, end_date, tenant_id),
        audit.c.logins,
        audit.c.api_calls,
        audit.c.failed_auth,
        audit.c.webhook_deliveries,
    ]
    async with db_circuit_guard():
        result = await db.execute(select(*columns))
```

### DO: Read `audit_logs` once per statement, with conditional aggregates

Five dashboard aggregates read the SAME `audit_logs` row set — `tenant_id = X
AND created_at BETWEEN start AND end`:

| aggregate | predicate |
|---|---|
| `logins` | `action IN ('auth.login', 'auth.sso_login')` |
| `api_calls` | *(none — the unfiltered superset)* |
| `failed_auth` | `action = 'auth.login' AND coalesce(details->>'success','true') = 'false'` |
| `webhook_deliveries` | `action LIKE 'webhook.%'` |
| `active_users` | `count(DISTINCT user_id)` over the `logins` predicate |

They were five separate scalar subqueries, so one window was scanned five
times — four of them probing `idx_audit_tenant_action` and then fetching the
heap for `details`/`user_id`. `_audit_grouped_subquery` makes them five
`count(...) FILTER (WHERE ...)` aggregates over ONE pass:

```python
# ONE scan serving all five
select(
    func.count(AuditLog.id).filter(login_actions).label("logins"),
    func.count(AuditLog.id).label("api_calls"),
    func.count(AuditLog.id).filter(failed_auth).label("failed_auth"),
    func.count(AuditLog.id).filter(AuditLog.action.like("webhook.%")).label("webhook_deliveries"),
    func.count(func.distinct(AuditLog.user_id)).filter(login_actions).label("active_users"),
).where(criterion(AuditLog, tenant_id), AuditLog.created_at >= start, AuditLog.created_at <= end_date).subquery()
```

The access path is `idx_audit_logs_tenant_created_id (tenant_id, created_at,
id)` — its leading two columns are exactly the window predicate, so the
planner range-scans the window instead of probing the action index five
times. The subquery is an aggregate with **no GROUP BY**, so it yields
exactly one row (all zeros for an empty window), which keeps the merged
statement's `result.one()` valid.

**Keep the predicates byte-identical when editing either side.** There is ONE
activity spelling in this module (`_audit_grouped_subquery`); the old
`_activity_subqueries` builder was deleted because it had no production caller
and restated these predicates in a form nothing executed.

The one remaining dual spelling is the **parity oracle**:
`_user_subqueries(audit=None)` builds `active_users` as its own scalar subquery
via `audit_logs.trail.login_activity_subquery`, and
`test_p4_audit_grouped_scan.py::TestTheParityOracle` compiles it against the
grouped scan's `FILTER`. Production NEVER takes that branch —
`_get_all_metrics` always passes `audit=`. It exists solely so a change to one
spelling that shifts the number is caught by a test. Do not route production
through it, and do not delete it as "dead": deleting it removes the only
independent check on the grouped scan.

### Revenue ratios: the denominator is the meaning

`average_revenue_per_user` divides `total_revenue` by **`total_users`**;
`average_revenue_per_subscriber` divides the same numerator by
**`total_subscriptions`**. Each field is named for its denominator.

They were previously the same number: ARPU divided by subscriptions, so it
coincided with a per-user figure only for tenants where every user holds
exactly one subscription. `total_users` was already in the same statement
(column 0), so the fix reads it from there — no extra column, no extra query,
and the positional layout is unchanged.

**Windows:** `total_revenue` is scoped to the requested range;
`total_users` and `total_subscriptions` are CURRENT snapshots. Both ratios
therefore mean "this range's revenue over the current base", which is the
standard ARPU reading and is stated in the schema descriptions. If a
per-range-active-user figure is ever needed, it needs a new numerator-scoped
denominator — not a different divisor on this one.

### Caching
- `get_metrics` caches per `(tenant_id, time_range)` via `cache_client`
- Cache key: `cache_client.tenant_key(f"dashboard:metrics:{time_range.value}", tenant_id)`
- A cold (or just-expired) key is **single-flighted** (P2-4) through
  `cache_client.read_through(...)` — the ONE read-through seam in
  `src/app/core/cache/read_through.py`, shared with the tier-policy read.
  The seam claims `{cache_key}:refresh`, lets one caller compute, and serves
  the rest the published payload; a waiter that outlives
  `_METRICS_REFRESH_WAIT_SECONDS` computes anyway rather than stalling.
- `_compute_metrics` is the `fetch` half and does **not** write to the cache:
  the seam owns publishing, which is what lets one place decide whether a
  computed value may be stored at all (`ReadThroughPolicy.should_cache`).
- The claim TTL (`_METRICS_REFRESH_LOCK_TTL`) and the wait budget
  (`_METRICS_REFRESH_WAIT_SECONDS`) are passed separately, and the seam
  refuses a policy where the wait outlives the claim — a waiter that outlives
  the claim it waits on would compute alongside the next claimer.

### Time Ranges
- `today` / `week` / `month` (default) / `quarter` / `year` / `all`
- `TimeRange.ALL` folds the min-created lookup into the merged statement

## Touch Points / Key Files

- Service: `src/app/modules/dashboard/service.py` - aggregation + caching
- Schemas: `src/app/modules/dashboard/schemas.py` - `UserMetrics`, `RevenueMetrics`, `ActivityMetrics`, `DashboardMetricsResponse`
- Router: `src/app/modules/dashboard/router.py` - `GET /dashboard?time_range=`

## JIT Index Hints

- Find subqueries: `rg -n "_subqueries" src/app/modules/dashboard/service.py`
- Find endpoints: `rg -n "@router\.(get|post|put|delete)" src/app/modules/dashboard/`

## Common Gotchas

- Every aggregate must be tenant-scoped — a missing `tenant_id` filter here leaks cross-tenant metrics
- Column layout is positional (`[4 user | 3 revenue | 4 activity]`); keep `_row_to_*` offsets in sync when adding aggregates
- Single-statement snapshot: all metrics reflect one point in time

## Pre-PR Checks

```bash
uv run mypy src/app/modules/dashboard/ && uv run ruff check src/app/modules/dashboard/
```
