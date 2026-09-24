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
    columns = [
        *self._user_subqueries(start, end_date, tenant_id),
        *self._revenue_subqueries(start, end_date, tenant_id),
        *self._activity_subqueries(start, end_date, tenant_id),
    ]
    async with db_circuit_guard():
        result = await db.execute(select(*columns))
```

### Caching
- `get_metrics` caches per `(tenant_id, time_range)` via `cache_client`
- Cache key: `cache_client.tenant_key(f"dashboard:metrics:{time_range.value}", tenant_id)`

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
