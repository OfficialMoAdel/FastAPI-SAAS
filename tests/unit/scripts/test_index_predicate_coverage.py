"""Tests for the index/predicate coverage checker.

Bead FastAPI_saas-nrgi.7. A coverage gate that cannot fail is worse than no
gate, because it is read as proof the hot lists stay indexed. These tests pin
the three things that make this one trustworthy:

* the btree COLUMN-ORDER rules actually reject the shapes the bead says are
  not coverage — a trailing column the query never filters, a non-leading
  equality column, and a DESC scan against an ASC column;
* the parser reads every index SQLAlchemy would create, including the
  ``__table_args__: tuple[...] = (...)`` annotated spelling this repo uses on
  ``User`` and ``UserSubscription``;
* the real repository currently has exactly the known gaps, so a future
  change to a hot predicate is caught the day it lands.

The ORDER of these matters more than it looks: the btree tests use hand-built
index/predicate pairs rather than the repo's real ones, so a mistake in the
shared coverage rule cannot be cancelled out by a coincidence in the schema.
"""
import ast
from pathlib import Path

import pytest

# scripts/ is not an installed package, but it IS a package on disk
# (scripts/checks/__init__.py), so the checker is importable as
# scripts.checks.index_predicate_coverage from the repository root.
from scripts.checks import index_predicate_coverage as checker

ROOT = Path(__file__).resolve().parents[3]


def mk_index(name, columns, ops=(), predicate=None):
    return checker.Index(name=name, columns=tuple(columns), ops=tuple(ops), predicate=predicate)


def mk_pred(**kw):
    base = {"module": "m", "model": "M", "entry": "e"}
    base.update(kw)
    return checker.Predicate(**base)


class TestEqualityPrefix:
    """Only EQUALITY-comparable columns form the leading lookup prefix."""

    def test_equality_columns_are_pinned(self):
        ix = mk_index("ix", ("tenant_id", "user_id", "created_at"))
        pred = mk_pred(required=("tenant_id", "user_id"), order_by=("created_at",))
        assert checker._leading_equality_prefix(ix, pred) == 2

    def test_range_column_is_not_pinned(self):
        """A leading column the query only RANGES over is not a lookup key.

        This is the bug that made a correctly-declared audit_logs index look
        uncovered: created_at appears in `where` as a date-range filter, so a
        parser that pins on mere membership over-counts the prefix and pushes
        the real ordering column out of the suffix.
        """
        ix = mk_index("ix", ("tenant_id", "created_at", "id"))
        pred = mk_pred(
            required=("tenant_id",),
            where=("tenant_id", "created_at"),
            order_by=("created_at", "id"),
            order_desc=True,
        )
        assert checker._leading_equality_prefix(ix, pred) == 1

    def test_unconstrained_leading_column_breaks_the_chain(self):
        """btree is ordered left to right: a gap stops every column after it."""
        ix = mk_index("ix", ("status", "tenant_id", "user_id"))
        pred = mk_pred(required=("tenant_id", "user_id"))
        assert checker._leading_equality_prefix(ix, pred) == 0


class TestOrderDirection:
    """DESC scans need a DESC (or fully-ASC, read backwards) ordering suffix."""

    def test_desc_ops_serve_desc_scan(self):
        ix = mk_index(
            "ix",
            ("tenant_id", "created_at", "id"),
            ops=(("created_at", "DESC"), ("id", "DESC")),
        )
        pred = mk_pred(required=("tenant_id",), order_by=("created_at", "id"), order_desc=True)
        assert checker.covers(ix, pred)

    def test_asc_ops_serve_desc_scan_by_backward_scan(self):
        ix = mk_index("ix", ("tenant_id", "created_at"))
        pred = mk_pred(required=("tenant_id",), order_by=("created_at",), order_desc=True)
        assert checker.covers(ix, pred)

    def test_mixed_direction_does_not_serve(self):
        """A partial reversal serves neither forward nor backward."""
        ix = mk_index("ix", ("tenant_id", "created_at", "id"), ops=(("created_at", "DESC"),))
        pred = mk_pred(required=("tenant_id",), order_by=("created_at", "id"), order_desc=True)
        assert not checker.covers(ix, pred)

    def test_desc_index_does_not_serve_asc_scan(self):
        ix = mk_index("ix", ("tenant_id", "created_at"), ops=(("created_at", "DESC"),))
        pred = mk_pred(required=("tenant_id",), order_by=("created_at",), order_desc=False)
        assert not checker.covers(ix, pred)


class TestCoverageRejectsNonCoverage:
    """The shapes the bead explicitly calls out as NOT coverage."""

    def test_trailing_column_absent_from_predicate_is_not_coverage(self):
        """(tenant_id, created_at) does not serve a (tenant_id, user_id) filter."""
        ix = mk_index("ix", ("tenant_id", "created_at"))
        pred = mk_pred(required=("tenant_id", "user_id"), order_by=("created_at",))
        assert not checker.covers(ix, pred)

    def test_equality_lookup_cannot_supply_a_sort(self):
        """An index whose columns are all pinned returns no ordered stream."""
        ix = mk_index("ix", ("tenant_id", "user_id"))
        pred = mk_pred(required=("tenant_id", "user_id"), order_by=("created_at",))
        assert not checker.covers(ix, pred)

    def test_missing_order_column_is_not_coverage(self):
        ix = mk_index("ix", ("tenant_id", "status", "created_at"))
        pred = mk_pred(required=("tenant_id",), order_by=("created_at",), order_desc=True)
        assert not checker.covers(ix, pred)

    def test_partial_index_never_covers_a_plain_list(self):
        """A partial index only serves a query implying its postgresql_where."""
        ix = mk_index("ix", ("tenant_id", "created_at"), predicate="status = 'failed'")
        pred = mk_pred(required=("tenant_id",), order_by=("created_at",), order_desc=True)
        assert not checker.covers(ix, pred)

    def test_no_order_by_needs_only_an_equality_lookup(self):
        ix = mk_index("ix", ("tenant_id", "user_id"))
        pred = mk_pred(required=("tenant_id", "user_id"), order_by=())
        assert checker.covers(ix, pred)


class TestParser:
    """The parser must not under-report, or the gate invents false gaps."""

    def test_reads_annotated_table_args(self):
        """`__table_args__: tuple[Index, ...] = (...)` is an AnnAssign.

        Reading only the plain spelling silently drops every index declared on
        User and UserSubscription, which is how a real covering index gets
        reported as a gap.
        """
        src = '''
from sqlalchemy import Index
class User(Base):
    __tablename__ = "users"
    __table_args__: tuple[Index, ...] = (
        Index("idx_users_tenant_deleted", "tenant_id", "is_deleted"),
    )
'''
        tree = ast.parse(src)
        cls = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)][0]
        value = next(
            v
            for v in (checker._table_args_value(s) for s in cls.body)
            if v is not None
        )
        found = [
            checker._index_from_call(e)
            for e in ast.walk(value)
            if isinstance(e, ast.Call)
        ]
        assert [i.name for i in found if i] == ["idx_users_tenant_deleted"]

    def test_reads_postgresql_ops_direction(self):
        src = '''
from sqlalchemy import Index
class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(
            "idx_audit_logs_tenant_created_id",
            "tenant_id", "created_at", "id",
            postgresql_ops={"created_at": "DESC", "id": "DESC"},
        ),
    )
'''
        tree = ast.parse(src)
        cls = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)][0]
        value = next(
            v
            for v in (checker._table_args_value(s) for s in cls.body)
            if v is not None
        )
        ix = [
            checker._index_from_call(e)
            for e in ast.walk(value)
            if isinstance(e, ast.Call)
        ][0]
        assert ix.ops == (("created_at", "DESC"), ("id", "DESC"))

    def test_real_models_expose_their_composites(self):
        """Spot-check a real model the gate depends on."""
        idx = checker.parse_indexes(ROOT / "src" / "app" / "modules" / "billing" / "models.py")
        names = {i.name: i.columns for i in idx["UserSubscription"]}
        assert names["idx_subscription_tenant_user_status"] == (
            "tenant_id",
            "user_id",
            "status",
        )


class TestRepositoryBaseline:
    """The real tree has exactly the gaps this audit recorded."""

    @pytest.fixture(scope="class")
    def result(self):
        all_indexes = {}
        for p in sorted((ROOT / "src" / "app" / "modules").glob("*/models.py")):
            all_indexes.update(checker.parse_indexes(p))
        return checker.check(checker.PREDICATES, all_indexes, verbose=False)

    def test_no_unexpected_gaps(self, result):
        """Only the audited, reported gaps may be present.

        A NEW uncovered predicate is the regression this gate exists to catch,
        so the expected set is pinned rather than merely counted.
        """
        expected = {
            "notifications.service.py:NotificationService.get_notifications",
            "webhooks.service.py:WebhookService.get_deliveries",
            "ai.service.py:ChatSessionService.get_user_sessions",
        }
        got = {pred.label for pred, _ in result.uncovered}
        assert got == expected

    def test_covered_predicates_name_their_serving_index(self):
        """Each passing predicate must be served by the index this audit names.

        Without this, a coverage rule that returned True for everything would
        satisfy test_no_unexpected_gaps by making every predicate 'a gap' —
        the other way a gate of this shape silently dies.
        """
        all_indexes = {}
        for p in sorted((ROOT / "src" / "app" / "modules").glob("*/models.py")):
            all_indexes.update(checker.parse_indexes(p))
        serving = {
            "audit_logs.service.py:AuditLogService.query_logs_with_total": (
                "idx_audit_logs_tenant_created_id"
            ),
            "billing.subscription_service.py:UserSubscriptionService.get_user_subscriptions": (
                "idx_subscription_tenant_user_status"
            ),
            "users.service.py:UserService.list_users": "idx_users_tenant_deleted",
            "storage.service.py:StorageService.list_files": "idx_files_tenant_created",
            "posts.service.py:PostService.get_posts_keyset": "idx_posts_tenant_user_created_id",
        }
        for pred in checker.PREDICATES:
            if pred.label not in serving:
                continue
            names = {i.name for i in all_indexes[pred.model] if checker.covers(i, pred)}
            assert serving[pred.label] in names, f"{pred.label} not served by {serving[pred.label]}"

    def test_every_predicate_names_a_real_entry_point(self):
        """A predicate pointing at a deleted service is silent rot.

        The method name is what proves the query still exists; the class name
        is recorded for the reader only, because a refactor that renames a
        class while keeping the method is not a broken predicate.
        """
        for pred in checker.PREDICATES:
            filename, _, symbol = pred.entry.partition(":")
            path = ROOT / "src" / "app" / "modules" / pred.module / filename
            assert path.exists(), f"{pred.module}/{filename} missing"
            method = symbol.rpartition(".")[2]
            assert f"def {method}(" in path.read_text(encoding="utf-8"), f"{pred.entry} missing"
