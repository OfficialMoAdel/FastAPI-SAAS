# Index / Predicate Audit (bead FastAPI_saas-nrgi.7)

**Date:** 2026-10-01
**Status:** analysis complete; **no migration created** (see [What remains](#what-remains-not-done-and-why)).
**Gate added:** `scripts/check_index_predicate_coverage.py` (static, no database).

This document is the deliverable. Its purpose is to stop a future query change
from silently losing its index, and to record which of the bead's own claims
survived contact with the source.

---

## 1. The scope contest: settled

The bead carried two conflicting claims. Both were re-located and both are
**wrong in the way stated**, and they are not describing the same table.

| Source of claim | Claim | Verdict |
|---|---|---|
| Plan (cited `billing/subscription_service.py`) | `user_subscriptions` filters `(tenant_id, user_id, status)` ordered by `created_at DESC` with no matching composite index | **The gap does not exist.** The composite index exists and does match. The claim's *ORDER BY* half is also wrong — the query emits no ORDER BY. |
| Review | "the real gap is `ai_chat_sessions` only" | **Directionally right, incomplete.** `ai_chat_sessions` is a genuine gap, but it is one of **three**, not the only one. |

**The two are describing different tables with different real answers**, which
is why they appear to contradict each other: the plan was looking at
`user_subscriptions` (adequately indexed), the review at `ai_chat_sessions`
(genuinely uncovered). Neither claim survives verbatim.

### The plan's claim, checked against source

`idx_subscription_tenant_user_status (tenant_id, user_id, status)` is declared
at `src/app/modules/billing/models.py:168-175` and created in migration
`src/migrations/versions/04_billing.py:153`:

```python
op.create_index("idx_subscription_tenant_user_status", "user_subscriptions",
                ["tenant_id", "user_id", "status"])
```

The emitted predicate (compiled offline, see §3) is:

```sql
SELECT ... FROM user_subscriptions
WHERE user_subscriptions.user_id = 7 AND user_subscriptions.tenant_id = '...'
```

Two leading equality columns, both pinned. The composite covers it exactly.
**The review's side of the contest is the one supported by source** — but see
§2 for the two tables it missed.

---

## 2. The real predicates vs. declared indexes

Every statement below was **compiled offline against the postgresql dialect**
(no connection; see §3), so these are the predicates the database actually
receives, not a reading of the Python.

| # | Table | Real WHERE (equality prefix) | ORDER BY | Serving index (column order) | Verdict |
|---|---|---|---|---|---|
| 1 | `audit_logs` | `tenant_id` (+ optional `user_id`, `action`, `entity_type`, `entity_id`, `created_at` range) | `created_at DESC, id DESC` | `idx_audit_logs_tenant_created_id (tenant_id, created_at, id)` DESC, DESC | **COVERED** |
| 2 | `notifications` | `tenant_id`, **`user_id`** (+ optional `status`, `created_at` range) | `created_at DESC` | none | **GAP** |
| 3 | `webhook_deliveries` | `tenant_id` (+ optional `webhook_endpoint_id`, `status`, `event_type`, `created_at` range) | `created_at DESC` | none | **GAP** |
| 4 | `ai_chat_sessions` | `tenant_id`, `user_id` | **none** | none | **GAP** |
| 5 | `user_subscriptions` | `tenant_id`, `user_id` | **none** | `idx_subscription_tenant_user_status (tenant_id, user_id, status)` | **COVERED** |
| 6 | `users` | `tenant_id`, `is_deleted` | none | `idx_users_tenant_deleted (tenant_id, is_deleted)` | **COVERED** |
| 7 | `files` | `tenant_id` (+ optional `user_id`, `mime_type`) | `created_at DESC` | `idx_files_tenant_created (tenant_id, created_at)` DESC | **COVERED** |
| 8 | `post` | `tenant_id`, `created_by_user_id`, `is_deleted` | `created_at DESC, id DESC` | `idx_posts_tenant_user_created_id (tenant_id, created_by_user_id, is_deleted, created_at, id)` DESC, DESC | **COVERED** |

**5 of 8 covered; 3 genuine gaps.** Two tables that the bead's framing might
suggest need work — `user_subscriptions` and `users` — are adequately indexed,
and saying so is the result that prevents wasted migrations.

### 2.1 The three gaps, specified

Each is stated precisely enough to write and EXPLAIN-validate, per the bead.

**GAP 1 — `notifications`. The real predicate is `(tenant_id, user_id)`, not `(tenant_id)`.**

The router always passes the caller: `src/app/modules/notifications/router.py:97`
passes `user_id=ctx.user_id` with no way to omit it. The service therefore
always filters on both columns. Existing indexes on this table:

- `idx_notification_tenant_status (tenant_id, status)` — the leading equality
  matches, but `status` is **optional** in this query, so this index gives a
  tenant-wide scan plus a sort, not a lookup.
- `idx_notifications_tenant_created_id (tenant_id, created_at, id)` — serves the
  unfiltered `tenant_id` case, but stops at `created_at`; with `user_id` in the
  predicate it cannot be a lookup on the second column, because `user_id` sits
  *after* the ordering column. This is exactly the column-order trap the bead
  warns about: an index on `(tenant_id, created_at)` does not serve a query
  filtering `(tenant_id, user_id)`.

Recommended:

```sql
CREATE INDEX idx_notifications_tenant_user_created
  ON notifications (tenant_id, user_id, created_at DESC, id DESC);
```

Insert cost: one more btree entry per notification row. `notifications` is
written once per email send, which is low-volume relative to reads.

**GAP 2 — `webhook_deliveries`. Tenant-only list is unserved.**

`idx_delivery_tenant_status_created (tenant_id, status, created_at)` exists but
leads with `tenant_id` then `status`; the unfiltered list (the default, no query
params) has no `status` equality, so `status` is an unconstrained leading
column that breaks the btree chain before `created_at`. No other composite
leads with `tenant_id`.

Recommended:

```sql
CREATE INDEX idx_delivery_tenant_created
  ON webhook_deliveries (tenant_id, created_at DESC);
```

Insert cost: this is the **most expensive recommendation in the document** —
`webhook_deliveries` is written on every delivery attempt and retry, which is
genuinely high volume. This is the one most likely to be rejected on write-cost
grounds, and it should be.

**GAP 3 — `ai_chat_sessions`. Confirmed by the review, and understated there.**

Only single-column indexes exist: `ix_ai_chat_sessions_tenant_id`,
`ix_ai_chat_sessions_user_id`, `ix_ai_chat_sessions_created_at`. The query
filters **both** `tenant_id` and `user_id`, so Postgres can use either one
alone; it cannot use a two-column lookup, and it sorts in memory.

Recommended:

```sql
CREATE INDEX idx_ai_chat_sessions_tenant_user
  ON ai_chat_sessions (tenant_id, user_id);
```

Insert cost: one more index entry per chat session. Sessions are capped at
`MAX_SESSIONS_PER_TENANT = 100` per tenant (`src/app/modules/ai/service.py:50`),
which bounds the table's growth and makes this cheap.

### 2.2 Two observations that are not index work

- **`get_user_subscriptions` emits no ORDER BY.** `checkouts.py:75` comments
  "Return the first active subscription (most recent)" and `checkouts.py:114`
  picks `active_subs[0]`, but the statement has no ORDER BY, so "most recent" is
  whatever Postgres returns first. `fetch_page_with_total` is called with
  `sort_columns=None`, and FastCRUD applies sorting only
  `if sort_columns:` (`fastcrud/fast_crud.py:669`). This is a **correctness**
  bug, not an index gap. Flagged, not fixed — it is outside this bead's scope
  and fixing it changes API response ordering.
- **`get_user_sessions` (AI) likewise emits no ORDER BY**, same mechanism.
  Same classification, same decision.

---

## 3. How the statements were obtained (and the limit)

`Base` is a plain `DeclarativeBase` (`src/app/core/db/database.py:26`), so
model metadata imports without a connection. Statements were built and compiled
with:

```python
stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
```

This yields the exact SQL text quoted above **with no database**. What it does
**not** give is the planner's decision: no `EXPLAIN`, no row counts, no timings,
no confirmation that the planner *chooses* the index over a sequential scan.
Every "COVERED" verdict in §2 is a statement about index *eligibility* under
btree column-order rules, not a measured plan.

Importing the models requires `POSTGRES_PASSWORD` and `SECRET_KEY` to be set
(they are required settings fields). The gate is therefore **pure AST** and
imports nothing from `src.app`, so it runs in the lint job with no credentials
and no database.

---

## 4. Assessment of the existing tooling: `src/scripts/verify_indexes.py`

The bead says `scripts/verify_indexes.py`. **That path does not exist.** A
repo-root glob returns no such file. The real file is
`src/scripts/verify_indexes.py` — this was independently reported by a sibling
and confirmed here before acting on it. Concluding "the tooling is absent"
from the root path alone would have been the wrong call.

**What it checks** (`src/scripts/verify_indexes.py:18-32`): it connects to a
live Postgres, runs
`SELECT indexname FROM pg_indexes WHERE tablename IN ('roles','role_permissions','user_roles')`,
and asserts that four names are present:
`ix_roles_tenant_id`, `ix_role_permissions_permission_id`,
`ix_user_roles_tenant_id`, `ix_user_roles_role_id`.

**Where it is right.** Its assertions do agree with the migration layer. All
four names are created in `src/migrations/versions/03_access_control.py` at
lines **77, 94, 110, 111** — verified directly, not taken on report. It is not
asserting anything false; its problem is scope and operability, not accuracy.

**Where it is unreliable.**

1. **It cannot run in CI.** It builds an async engine from settings and queries
   `pg_indexes` (lines 12-22). The lint job has no database, so this can never
   be a CI gate. A check that cannot run unattended is documentation.
2. **It covers the wrong tables.** Three RBAC tables. It touches **none** of
   the five hot lists this bead names (audit log, notification, webhook
   delivery, `ai_chat_sessions`, billing subscriptions). It cannot detect any
   regression in the queries that matter.
3. **It ignores column order.** It compares index *names* only. `idx_x(a,b,c)`
   and `idx_x(a,c,b)` are indistinguishable to it, so it would report success
   for an index that serves the wrong predicate. Given that column order is the
   central question in this bead, that is the decisive defect.
4. **It cannot fail on a new query.** Nothing in it is derived from the list
   services, so adding a filter or changing an ORDER BY leaves it green.
5. **It has no column list, no predicate, no direction.** Partial indexes
   (`postgresql_where`) and `postgresql_ops` direction are invisible to it.
6. **It uses `exit(1)`** inside a coroutine rather than returning a status, and
   prints rather than reporting — poor composability, minor next to the above.

**Decision: build alongside, do not repair.** Three reasons. First, it is a
*live-database* tool doing a *different job* (verify migrations were applied to
a running database) from a *static* tool (verify declared indexes cover declared
predicates) — merging them would force one of the two to give up the property
that makes it work, namely that only one needs a database. Second, its four
assertions are correct, so there is nothing to fix, only something to extend,
and extending it to the hot lists would require the connection it cannot have
in CI. Third, it is inside `src/scripts/`, which a sibling owns for this run.

**Complementarity is deliberate and is not double-counting:** the two tools
answer different questions and are wrong in different directions. `verify_indexes.py`
catches *drift between the migration and the live database*; the new gate catches
*drift between the source and the declared indexes*. Neither subsumes the other,
and both would be needed to cover the full failure space.

---

## 5. The durable check

`scripts/check_index_predicate_coverage.py`, wired into the lint job of
`.github/workflows/tests.yml` as step "Run index predicate coverage gate".

It parses every `src/app/modules/*/models.py` with `ast`, and compares the
declared indexes against an explicit `PREDICATES` table. Coverage is decided
under real btree column-order rules, not set membership:

- leading index columns must be **equality-pinned** by the query (a
  `created_at >= x` range filter is *not* a pinned key);
- the remaining columns must match the `ORDER BY` **in the same direction**,
  reading `postgresql_ops` from the declaration rather than assuming;
- a partial index (`postgresql_where`) never covers a plain list query.

**Parser soundness was verified, not assumed.** The AST parser was
cross-validated against SQLAlchemy's own `Base.metadata`, comparing by column
tuple across all 40 tables: **130 indexes checked, 0 missed**. This caught a
real bug during development — the repo uses the annotated spelling
`__table_args__: tuple[Index, ...] = (...)` on `User` and `UserSubscription`,
and a parser reading only the plain `ast.Assign` form silently dropped every
index on those two models, reporting two false gaps.

**Falsifiability was demonstrated, not claimed.** The `user_id` index on
`ai_chat_sessions` was temporarily removed (backup taken to `/tmp/x.bak` first);
the gate immediately reported `ai_chat_sessions` as uncovered and exited 1. The
file was restored as the immediate next action. `git diff` on
`src/app/modules/ai/models.py` is empty and `git status --porcelain` reports
nothing for that path. **No mutation residue remains anywhere in the tree.**

Tests: `tests/unit/scripts/test_index_predicate_coverage.py`, 18 passing. They
pin the rejection cases (trailing column absent from the predicate, non-leading
equality, mixed sort direction, partial index, equality lookup asked to supply a
sort), the parser behaviours above, and the repository baseline.

---

## <a name="what-remains-not-done-and-why"></a>What remains (not done, and why)

**No migration was created.** Postgres is unavailable in this environment, so
`EXPLAIN` cannot be run. A migration validated by nothing is precisely the
"index justified by intuition" the bead forbids. The three recommendations in
§2.1 are specified but not written.

To adopt any of them, on a representative dataset:

1. `ANALYZE` the target table so the planner has real statistics.
2. Capture the baseline: `EXPLAIN (ANALYZE, BUFFERS)` the real statement —
   take the exact SQL from §3 — and record the node type, the actual vs. planned
   row counts, and whether it is a `Seq Scan`.
3. Create the index (`CREATE INDEX CONCURRENTLY` in production, to avoid
   locking writes).
4. `ANALYZE` again, then re-run the identical `EXPLAIN (ANALYZE, BUFFERS)`.
5. Accept only if the plan moves to the index **and** the measured time
   improves. On a small table a `Seq Scan` may be *correct* and cheaper; that
   is a valid outcome and means the index should not be added.
6. Re-run the gate. It will now report those predicates as covered, so the
   `PREDICATES` expectations in the test baseline need updating in the same
   commit.

**GAP 2 (`webhook_deliveries`) should be decided on write cost first.** It is
the only recommendation on a genuinely high-write table, and the others are
cheap by comparison. If the delivery table is large enough that a sequential
scan over the tenant's rows is already cheap, the honest answer is to add
nothing.

**Not measured, and not estimated:** row counts, index sizes, planner choices,
timings. No such number appears anywhere in this document.

## Open questions

- `checkouts.py:75` / `:114` select `active_subs[0]` while calling a query
  with no ORDER BY. If "most recent" is intended, that is a live correctness
  bug independent of indexing. Deliberately left unfixed and unreported outside
  this document — it changes API response ordering and belongs to whoever owns
  the billing router.
- `ai_chat_sessions` and `user_subscriptions` both list without ordering. If
  pagination stability matters (rows shifting between pages), the fix is
  keyset pagination, not an index — and a composite ending in `created_at, id`
  would be required to make it correct. Out of scope here; recorded because it
  is the natural follow-up and it interacts with GAP 3's index.
