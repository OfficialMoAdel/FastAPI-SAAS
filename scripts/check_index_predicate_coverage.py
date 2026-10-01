"""Enforce that every hot list query keeps a covering composite index.

WHY THIS GATE EXISTS
--------------------
Bead FastAPI_saas-nrgi.7. The failure mode this prevents is silent: a list
service changes its WHERE or ORDER BY, the query keeps working, and the table
quietly loses the index that was the only reason the query was cheap. Nothing
in the test suite notices, because a correct answer from a sequential scan is
still a correct answer.

An index that does not match a real predicate is write amplification with no
read benefit, so the gate is written in the direction that punishes BOTH
mistakes: adding an index nobody filters by, and dropping the index for a
filter that exists.

WHAT IT CHECKS
--------------
A small, explicit set of ``PREDICATES`` — the real, routed, tenant-scoped list
queries. For each one the gate records:

  * ``model``     — the ORM class, used to find the model file.
  * ``entry``     — the service method that builds the statement.
  * ``where``     — the columns a REQUEST may filter by (optionality from
                    the source: a column inside ``if x is not None:`` is
                    optional, a column always added is mandatory).
  * ``required``  — columns present in EVERY emitted statement.
  * ``order_by``  — ORDER BY columns in precedence order.

Coverage is decided by the index that the planner would actually choose, and
that means COLUMN ORDER, not set membership. PostgreSQL uses a btree index for
equality columns as a leading prefix and can then use later columns for
ordering only if the ordering matches; a column that is neither filtered nor
ordered does not narrow the scan. So for each candidate index:

  * every required WHERE column must appear in the index (position-free for
    the leading equality prefix), and
  * the ORDER BY prefix that the index can serve must not be broken.

Concretely ``(tenant_id, created_at)`` does NOT serve a query filtering
``(tenant_id, user_id, status)``; ``(tenant_id, status, created_at)`` does not
serve an ordered scan for a query whose leading WHERE is ``(tenant_id)`` with
no status equality, because ``status`` is an inequality on the ordering path.

WHY IT IS PURE AST
------------------
Importing ``src.app.models`` requires the settings object to validate
(``POSTGRES_PASSWORD`` and ``SECRET_KEY`` are required fields), so a checker
that imported the app could not run in the lint job without credentials and
could not run on a developer machine without a ``.env``. A gate that needs a
database or a secret to run is a gate that stops being run. This one parses
``models.py`` with :mod:`ast` and needs nothing but the standard library.

It is static on purpose: the alternative (compile each statement and diff
against a live ``pg_indexes``) is what ``src/scripts/verify_indexes.py``
already does, and it is the thing this bead found untrustworthy — see
``docs/structure/INDEX_PREDICATE_AUDIT.md`` for the assessment and for the
EXPLAIN validation that is still owed.

Usage:
    uv run python scripts/check_index_predicate_coverage.py
    uv run python scripts/check_index_predicate_coverage.py --verbose

Exit codes:
    0 - every predicate has a column-order-correct covering index
    1 - a predicate is uncovered, or a declared index is redundant
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "src" / "app" / "modules"


@dataclass(frozen=True)
class Index:
    """A declared index: an explicit ``Index(...)`` or a ``index=True`` column."""

    name: str
    columns: tuple[str, ...]
    unique: bool = False
    #: ``postgresql_where=`` — a partial index only serves its own predicate.
    predicate: str | None = None
    #: ``postgresql_ops={"created_at": "DESC"}`` — the declared sort direction
    #: of each column. Absent means the default (ASC).
    ops: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Predicate:
    """One real list query, as the database actually receives it."""

    #: Module directory name, e.g. ``audit_logs``.
    module: str
    #: ORM class name whose model file declares the indexes.
    model: str
    #: ``file:function`` of the service method that builds the statement.
    entry: str
    #: WHERE columns a request may filter by, in source order.
    where: tuple[str, ...] = ()
    #: Columns present in EVERY emitted statement (the mandatory prefix).
    required: tuple[str, ...] = ()
    #: WHERE columns compared with ``==`` (pinned to a constant). These are the
    #: columns a btree can use as a leading equality lookup key; a RANGE filter
    #: (e.g. ``created_at >= x``) must NOT be listed here. Defaults to
    #: ``required`` when not given, which is correct for predicates whose
    #: required columns are all equality comparisons.
    equality: tuple[str, ...] | None = None
    #: ORDER BY columns in precedence order, as bare column names.
    order_by: tuple[str, ...] = ()
    #: True when ORDER BY is DESC. An index whose trailing columns are declared
    #: ASC cannot serve a DESC scan; Postgres only scans backward when EVERY
    #: ordering column is reversed, so a mixed ASC/DESC index does not help.
    order_desc: bool = False
    #: Why this query is hot, and what breaks if the index is lost.
    note: str = ""
    #: Set when this entry is a deliberate RECOMMENDATION, not current state.
    recommended: str | None = None

    @property
    def eq_cols(self) -> tuple[str, ...]:
        return self.equality if self.equality is not None else self.required

    @property
    def label(self) -> str:
        return f"{self.module}.{self.entry}"


# ---------------------------------------------------------------------------
# The predicate table.
#
# Every entry below was derived by reading the service and compiling the
# statement offline against the postgresql dialect; the emitted SQL is quoted
# in docs/structure/INDEX_PREDICATE_AUDIT.md next to the index that serves it.
# `required` is the subset of `where` that is unconditionally in the WHERE.
# ---------------------------------------------------------------------------
PREDICATES: tuple[Predicate, ...] = (
    Predicate(
        module="audit_logs",
        model="AuditLog",
        entry="service.py:AuditLogService.query_logs_with_total",
        where=("tenant_id", "user_id", "action", "entity_type", "entity_id", "created_at"),
        required=("tenant_id",),
        order_by=("created_at", "id"),
        order_desc=True,
        note="GET /audit-logs. RLS-protected, tenant-only, deterministic id tie-breaker.",
    ),
    Predicate(
        module="notifications",
        model="Notification",
        entry="service.py:NotificationService.get_notifications",
        where=("tenant_id", "user_id", "status", "created_at"),
        required=("tenant_id", "user_id"),
        order_by=("created_at",),
        order_desc=True,
        note=(
            "GET /notifications. The router ALWAYS passes user_id=ctx.user_id "
            "(router.py:97), so the real predicate is (tenant_id, user_id)."
        ),
    ),
    Predicate(
        module="webhooks",
        model="WebhookDelivery",
        entry="service.py:WebhookService.get_deliveries",
        where=("tenant_id", "webhook_endpoint_id", "status", "event_type", "created_at"),
        required=("tenant_id",),
        order_by=("created_at",),
        order_desc=True,
        note="GET /webhooks/deliveries. endpoint_id/status/event_type are optional filters.",
    ),
    Predicate(
        module="ai",
        model="AIChatSession",
        entry="service.py:ChatSessionService.get_user_sessions",
        where=("tenant_id", "user_id"),
        required=("tenant_id", "user_id"),
        order_by=(),
        note=(
            "GET /ai/sessions. Delegates to FastCRUD via fetch_page_with_total "
            "with sort_columns=None, so the statement carries NO ORDER BY at all."
        ),
    ),
    Predicate(
        module="billing",
        model="UserSubscription",
        entry="subscription_service.py:UserSubscriptionService.get_user_subscriptions",
        where=("tenant_id", "user_id"),
        required=("tenant_id", "user_id"),
        order_by=(),
        note=(
            "GET /billing/subscriptions. Also sort_columns=None → no ORDER BY. "
            "checkouts.py:75 comments 'most recent' but the statement does not sort."
        ),
    ),
    Predicate(
        module="users",
        model="User",
        entry="service.py:UserService.list_users",
        where=("tenant_id", "is_deleted"),
        required=("tenant_id", "is_deleted"),
        order_by=(),
        note="GET /users. is_deleted defaults to False and is always sent.",
    ),
    Predicate(
        module="storage",
        model="File",
        entry="service.py:StorageService.list_files",
        where=("tenant_id", "user_id", "mime_type"),
        required=("tenant_id",),
        order_by=("created_at",),
        order_desc=True,
        note="GET /files. Two shapes: unfiltered (tenant only) and filtered (user/mime).",
    ),
    Predicate(
        module="posts",
        model="Post",
        entry="service.py:PostService.get_posts_keyset",
        where=("tenant_id", "created_by_user_id", "is_deleted"),
        required=("tenant_id", "created_by_user_id", "is_deleted"),
        order_by=("created_at", "id"),
        order_desc=True,
        note="GET /posts keyset. is_deleted is a literal is_(False), not a parameter.",
    ),
)


@dataclass
class Report:
    uncovered: list[tuple[Predicate, str]] = field(default_factory=list)
    unused: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    parsed: int = 0


# ---------------------------------------------------------------------------
# models.py parsing
# ---------------------------------------------------------------------------
def _column_name(node: ast.expr) -> str | None:
    """Return the column name from ``"name"`` or ``Col.attr`` / ``Col["name"]``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
        return node.value.attr
    return None


def _dialect_where(call: ast.Call) -> str | None:
    """Return the ``postgresql_where=`` text of an ``Index(...)`` call, if any."""
    for kw in call.keywords:
        if kw.arg == "postgresql_where":
            try:
                return ast.unparse(kw.value)
            except Exception:  # pragma: no cover - unparse is total in 3.9+
                return "<unparseable>"
    return None


def _dialect_ops(call: ast.Call) -> tuple[tuple[str, str], ...]:
    """Return the ``postgresql_ops={...}`` mapping of an ``Index(...)`` call.

    The direction decides whether the index can serve a DESC scan, so it is
    read rather than assumed: an index declared without ``postgresql_ops`` is
    ASC, and an ASC column cannot answer ``ORDER BY created_at DESC``.
    """
    for kw in call.keywords:
        if kw.arg != "postgresql_ops" or not isinstance(kw.value, ast.Dict):
            continue
        pairs: list[tuple[str, str]] = []
        for k, v in zip(kw.value.keys, kw.value.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            # ``postgresql_ops={"created_at": "DESC"}`` is written as a bare
            # string, and unparse renders it as the repr ``'DESC'``. Compare
            # against a canonical token so the direction check is not decided
            # by quoting.
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                pairs.append((k.value, v.value.upper()))
                continue
            try:
                pairs.append((k.value, ast.unparse(v).upper().strip("'\"")))
            except Exception:  # pragma: no cover - unparse is total in 3.9+
                continue
        return tuple(pairs)
    return ()


def _table_args_value(stmt: ast.stmt) -> ast.expr | None:
    """Return the value of a ``__table_args__`` assignment, or None.

    The repo uses BOTH spellings — plain ``__table_args__ = (...)`` and the
    annotated ``__table_args__: tuple[Index, ...] = (...)`` (User and
    UserSubscription both use the annotated form). Reading only the plain form
    silently drops every index those two models declare, which would make the
    gate report a false gap.
    """
    if isinstance(stmt, ast.Assign):
        if any(isinstance(t, ast.Name) and t.id == "__table_args__" for t in stmt.targets):
            return stmt.value
        return None
    if isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Name) and stmt.target.id == "__table_args__":
            return stmt.value
    return None


def _index_from_call(elt: ast.Call) -> Index | None:
    """Build an :class:`Index` from an ``Index(...)`` call, or None."""
    fn = elt.func
    fname = getattr(fn, "id", None) or getattr(fn, "attr", None)
    if fname != "Index" or not elt.args:
        return None
    name = _column_name(elt.args[0])
    if not name:
        return None
    cols = tuple(c for c in (_column_name(a) for a in elt.args[1:]) if c)
    if not cols:
        # Index("name", <sql expression>) — no column list to reason about.
        return Index(name=name, columns=())
    return Index(
        name=name,
        columns=cols,
        unique=any(
            kw.arg == "unique" and getattr(kw.value, "value", False) for kw in elt.keywords
        ),
        predicate=_dialect_where(elt),
        ops=_dialect_ops(elt),
    )


def _column_index(stmt: ast.AnnAssign, owner: str) -> Index | None:
    """Build an :class:`Index` from ``col: Mapped[...] = mapped_column(index=True)``."""
    if not isinstance(stmt.value, ast.Call):
        return None
    if getattr(stmt.value.func, "id", None) != "mapped_column":
        return None
    kws = {kw.arg: kw.value for kw in stmt.value.keywords}
    if not any(
        getattr(kws.get(k), "value", False) is True for k in ("index", "unique")
    ):
        return None
    if not isinstance(stmt.target, ast.Name):
        return None
    return Index(
        name=f"ix_{owner.lower()}_{stmt.target.id}",
        columns=(stmt.target.id,),
        unique=bool(getattr(kws.get("unique"), "value", False)),
    )


def parse_indexes(models_py: Path) -> dict[str, list[Index]]:
    """Return ``{class_name: [Index, ...]}`` declared in a models.py.

    Reads explicit ``Index(...)`` entries from ``__table_args__`` and implicit
    single-column indexes from ``mapped_column(..., index=True)``. A unique
    constraint is included: Postgres gives it a btree and it serves the same
    predicates as a plain index.
    """
    tree = ast.parse(models_py.read_text(encoding="utf-8"), filename=str(models_py))
    result: dict[str, list[Index]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        indexes: list[Index] = []
        for stmt in node.body:
            value = _table_args_value(stmt)
            if value is not None:
                for elt in ast.walk(value):
                    if isinstance(elt, ast.Call) and (ix := _index_from_call(elt)) is not None:
                        indexes.append(ix)
            elif isinstance(stmt, ast.AnnAssign) and (
                ix := _column_index(stmt, node.name)
            ) is not None:
                indexes.append(ix)
        if indexes:
            result[node.name] = indexes
    return result


# ---------------------------------------------------------------------------
# Coverage decision
# ---------------------------------------------------------------------------
def _leading_equality_prefix(index: Index, pred: Predicate) -> int:
    """How many LEADING index columns the WHERE clause pins to a constant.

    A column counts only if the query compares it for EQUALITY. A range
    predicate (``created_at >= x``) is a column the index can use to bound a
    scan, but it is NOT a pinned lookup key, so it must not be counted here —
    counting it would push a real ordering column out of the suffix and make a
    correctly-declared index look uncovered.

    The prefix property matters: a leading index column the query does not
    constrain breaks the chain for every column after it, because btree is
    ordered left to right.
    """
    eq = pred.eq_cols
    n = 0
    for col in index.columns:
        if col in eq:
            n += 1
        else:
            break
    return n


def _serves_order(index: Index, pred: Predicate, pinned: int) -> bool:
    """True when the index can supply ``pred.order_by`` after the pinned prefix.

    Once the equality prefix is consumed, btree delivers rows in the index's own
    column order, so the remaining columns must match the requested ordering
    exactly. This repo declares its ordering indexes with
    ``postgresql_ops={"created_at": "DESC"}``; an ASC column cannot serve a
    DESC scan unless the whole suffix is reversed, which is why a single ASC
    column in front of a DESC one is not a match.
    """
    if not pred.order_by:
        # No ORDER BY: an equality lookup is all that is needed, and the
        # pinned-prefix check has already established that.
        return True
    suffix = index.columns[pinned:]
    wanted = list(pred.order_by)
    if len(suffix) < len(wanted):
        return False
    if suffix[: len(wanted)] != tuple(wanted):
        return False
    # Direction. A btree is read backwards only when EVERY ordering column is
    # reversed, so a DESC scan is served either by an all-ASC index (read
    # backwards) or by an index whose ordering columns are each declared DESC.
    # A mixed ASC/DESC suffix serves neither — which is exactly why
    # ``postgresql_ops`` is read from the declaration instead of assumed.
    ops = dict(index.ops)
    directions = [ops.get(col, "ASC") for col in wanted]
    if pred.order_desc:
        return all(d == "ASC" for d in directions) or all(d == "DESC" for d in directions)
    return all(d == "ASC" for d in directions)


def covers(index: Index, pred: Predicate) -> bool:
    """True when ``index`` serves ``pred`` under btree column-order rules."""
    if not index.columns:
        return False
    if index.predicate is not None:
        # A partial index only serves a query that implies its own
        # postgresql_where. None of the tracked list queries filter on a
        # partial predicate (e.g. status='failed'), so it can never be the
        # covering index here.
        return False
    pinned = _leading_equality_prefix(index, pred)
    if pinned < len(pred.required):
        return False
    return _serves_order(index, pred, pinned)


def check(predicates: tuple[Predicate, ...], all_indexes: dict[str, list[Index]], verbose: bool) -> Report:
    report = Report()
    for pred in predicates:
        declared = all_indexes.get(pred.model, [])
        report.parsed += len(declared)
        matched = [ix for ix in declared if covers(ix, pred)]
        if matched:
            if verbose:
                best = matched[0]
                print(f"  OK   {pred.label}  <- {best.name}({','.join(best.columns)})")
            continue
        # No index serves it. Say which declared index came CLOSEST so the
        # message is actionable rather than "nothing found".
        near = [
            ix
            for ix in declared
            if ix.columns and ix.columns[0] == (pred.required[0] if pred.required else None)
        ]
        detail = (
            f"nearest={near[0].name}({','.join(near[0].columns)})" if near else "no index leads with "
            + (pred.required[0] if pred.required else "<required col>")
        )
        if pred.recommended:
            detail += f" | recommended: {pred.recommended}"
        report.uncovered.append((pred, detail))
    return report


# Predicates measured uncovered on 2026-10-01 and tracked as real, EXPLAIN-
# pending debt. The gate is a RATCHET, not a to-do list: these are the entries
# it is allowed to report, and a predicate NOT listed here failing is what
# turns the build red. That is the property worth having - a list service that
# changes its WHERE or ORDER BY and silently loses its index is caught the
# moment it happens, which no test can do because a sequential scan still
# returns correct rows.
#
# Each entry is a finding, not an excuse. Two of the three were sized against
# write cost, and webhook_deliveries may legitimately get no index at all:
# it is written on every delivery attempt and every retry, so the read saving
# has to earn that. See docs/structure/INDEX_PREDICATE_AUDIT.md.
ACKNOWLEDGED_GAPS: dict[str, str] = {
    "ai.service.py:ChatSessionService.get_user_sessions": (
        "AIChatSession has only single-column indexes; the query filters both "
        "tenant_id and user_id so neither alone is a 2-column lookup. Cheap to "
        "add: sessions are capped at MAX_SESSIONS_PER_TENANT=100."
    ),
    "notifications.service.py:NotificationService.get_notifications": (
        "get_notifications ALWAYS passes user_id=ctx.user_id, but the existing "
        "idx_notifications_tenant_created_id stops at created_at, so user_id "
        "breaks the btree chain. Needs (tenant_id, user_id, created_at DESC, "
        "id DESC). One btree entry per email send."
    ),
    "webhooks.service.py:WebhookService.get_deliveries": (
        "The tenant-only delivery list is unserved: "
        "idx_delivery_tenant_status_created breaks the chain because status is "
        "an OPTIONAL leading column. HIGHEST WRITE COST of the three - written "
        "on every attempt and retry - so this one may legitimately get no index."
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", "-v", action="store_true", help="print each covered predicate")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail on acknowledged gaps too (for when they are finally closed)",
    )
    args = ap.parse_args(argv)

    all_indexes: dict[str, list[Index]] = {}
    for models_py in sorted(MODULES_DIR.glob("*/models.py")):
        try:
            all_indexes.update(parse_indexes(models_py))
        except SyntaxError as exc:  # pragma: no cover - parse failure is a build break
            print(f"FAIL could not parse {models_py}: {exc}", file=sys.stderr)
            return 1

    report = check(PREDICATES, all_indexes, args.verbose)

    if args.verbose:
        print()

    for pred, detail in report.uncovered:
        print(
            f"FAIL {pred.label}\n"
            f"     table={pred.model} where={list(pred.where)} "
            f"required={list(pred.required)} order_by={list(pred.order_by)}\n"
            f"     {detail}\n"
            f"     {pred.note}",
            file=sys.stderr,
        )

    blocking = [(p, d) for p, d in report.uncovered if p.label not in ACKNOWLEDGED_GAPS]
    acknowledged = [(p, d) for p, d in report.uncovered if p.label in ACKNOWLEDGED_GAPS]

    if blocking:
        print(
            f"\n{len(blocking)} hot list predicate(s) lost covering-index eligibility and are "
            f"NOT tracked as known debt. Fix the query's index, or add a reasoned "
            f"ACKNOWLEDGED_GAPS entry if the gap is real. See docs/structure/INDEX_PREDICATE_AUDIT.md.",
            file=sys.stderr,
        )
        return 1

    if acknowledged:
        if args.strict:
            print(
                f"\n--strict: {len(acknowledged)} acknowledged gap(s) still open: "
                f"{', '.join(p.label for p, _ in acknowledged)}",
                file=sys.stderr,
            )
            return 1
        print(
            f"OK  {len(PREDICATES) - len(acknowledged)}/{len(PREDICATES)} hot list predicates have a "
            f"covering index; {len(acknowledged)} tracked gap(s) acknowledged: "
            f"{', '.join(p.label for p, _ in acknowledged)}"
        )
        print("    Each needs EXPLAIN validation before an index is added; see docs/structure/INDEX_PREDICATE_AUDIT.md")
        return 0

    print(f"OK  all {len(PREDICATES)} hot list predicates have a covering index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
