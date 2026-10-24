"""The gate must catch a same-name/different-definition index, and must not
invent one.

`scripts/checks/index_definition_consistency.py` exists because Alembic
compares indexes by NAME and this repo declares no ``naming_convention``
(``src/app/core/db/base.py`` and ``src/migrations/env.py`` both omit one), so
an index that the ORM and the migration define differently produces NO
autogenerate diff. The first instance was ``idx_webhook_endpoint_active``:
the migration created ``(tenant_id, is_active)`` while the ORM declared a
single-column partial.

Both directions are asserted. The "must not invent" half is the one that
matters for trust: three earlier versions of the parser each reported a false
conflict, and a gate that cries wolf on a correct index gets ignored.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "checks" / "index_definition_consistency.py"


def _load():
    spec = importlib.util.spec_from_file_location("_index_def_consistency", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load()


class TestDefinitionComparison:
    """What counts as 'the same index'."""

    def test_identical_definitions_match(self):
        a = gate.IndexDef("i", ("tenant_id", "is_active"), "", False, "orm")
        b = gate.IndexDef("i", ("tenant_id", "is_active"), "", False, "mig")
        assert gate.same_definition(a, b)

    def test_column_order_is_part_of_the_definition(self):
        """A btree on (tenant_id, is_active) does not serve (is_active).

        Column order is not a formatting detail, so swapping it is a
        different index and must be reported.
        """
        a = gate.IndexDef("i", ("tenant_id", "is_active"), "", False, "orm")
        b = gate.IndexDef("i", ("is_active", "tenant_id"), "", False, "mig")
        assert not gate.same_definition(a, b)

    def test_partial_predicate_is_part_of_the_definition(self):
        a = gate.IndexDef("i", ("status",), "status = 'failed'", False, "orm")
        b = gate.IndexDef("i", ("status",), "", False, "mig")
        assert not gate.same_definition(a, b)

    def test_uniqueness_is_part_of_the_definition(self):
        """A unique index enforces something a plain one does not."""
        a = gate.IndexDef("uq", ("email",), "", True, "orm")
        b = gate.IndexDef("uq", ("email",), "", False, "mig")
        assert not gate.same_definition(a, b)

    def test_two_partially_unique_indexes_declared_identically_match(self):
        """The false positive this gate actually produced, pinned.

        ``uq_user_email_tenant`` is a partial UNIQUE index in BOTH layers.
        A parser that ignored ``unique=True`` reported it as a conflict.
        """
        a = gate.IndexDef("uq", ("email", "tenant_id"), "is_deleted = false", True, "orm")
        b = gate.IndexDef("uq", ("email", "tenant_id"), "is_deleted = false", True, "mig")
        assert gate.same_definition(a, b)


class TestPredicateSpellings:
    """Three spellings of postgresql_where occur in this repo."""

    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("bare_string", 'Index("i", "s", postgresql_where="s = 1")'),
            ("text_call", 'Index("i", "s", postgresql_where=text("s = 1"))'),
            ("sa_text_call", 'Index("i", "s", postgresql_where=sa.text("s = 1"))'),
        ],
    )
    def test_all_three_spellings_yield_the_same_predicate(self, label, source, tmp_path):
        models = tmp_path / "models.py"
        models.write_text(f"from sqlalchemy import Index, text\nimport sqlalchemy as sa\n{source}\n", encoding="utf-8")
        found = gate.parse_orm_indexes(models)
        assert found, f"{label} produced no index"
        assert found[0].partial == "s = 1", f"{label} lost the predicate"


class TestTheRealRepository:
    """The tree as it stands: one definition per shared name."""

    def test_no_shared_name_resolves_to_two_definitions(self):
        report = gate.build_report(verbose=False)
        assert report.conflicts == [], (
            "same index name, different definition between ORM and migration: "
            f"{[c[0] for c in report.conflicts]}"
        )

    def test_the_gate_exits_zero_on_the_current_tree(self, capsys):
        assert gate.main([]) == 0
        out = capsys.readouterr().out
        assert "OK" in out
        assert "FAIL" not in out

    def test_it_actually_inspected_indexes(self):
        """A gate that parses nothing returns OK and enforces nothing."""
        report = gate.build_report(verbose=False)
        assert len(report.shared) >= 20, f"only {len(report.shared)} shared names parsed"

    def test_stripe_subscription_unique_index_is_declared_in_both_layers(self):
        """The P1-2 parity fix stays pinned.

        ``uq_user_subscriptions_stripe_sub`` was created by 04_billing.py from
        day one but was never declared on the ORM model, so a schema built
        from ``Base.metadata.create_all`` — tests/conftest.py and
        scripts/init_test_db.py both do this — had NO unique constraint on
        ``user_subscriptions.stripe_subscription_id`` while production does.
        That is the split-brain this gate exists for, in the one direction a
        name-only check cannot see (the name was simply absent from the ORM).

        Declaring it moves the name into ``shared`` and therefore into
        enforcement: a future edit that drops the predicate, the unique flag or
        the column now fails the gate above instead of silently forking the
        schema again.
        """
        orm = {i.name: i for i in gate.parse_orm_indexes(_REPO / "src/app/modules/billing/models.py")}
        assert "uq_user_subscriptions_stripe_sub" in orm, "ORM no longer declares the Stripe subscription unique index"
        definition = orm["uq_user_subscriptions_stripe_sub"]
        assert definition.columns == ("stripe_subscription_id",)
        assert definition.unique is True
        assert definition.partial == "stripe_subscription_id IS NOT NULL"

        report = gate.build_report(verbose=False)
        assert "uq_user_subscriptions_stripe_sub" in report.shared, "index is not shared across both layers"
        assert "uq_user_subscriptions_stripe_sub" not in list(report.migration_only)


class TestItFailsOnTheDefectItExistsFor:
    def test_a_temporary_collision_is_reported(self, tmp_path):
        """End-to-end through build_report, on a synthetic tree.

        Not a mutation of the real models file: the parser is fed a fixture
        pair, so the check is exercised without leaving a falsification edit
        anywhere in the repository.
        """
        models = tmp_path / "models.py"
        models.write_text(
            "from sqlalchemy import Index\n"
            '__table_args__ = (Index("collide", "tenant_id", "is_active"),)\n',
            encoding="utf-8",
        )
        migration_dir = tmp_path / "versions"
        migration_dir.mkdir(parents=True, exist_ok=True)
        migration = migration_dir / "01_x.py"
        migration.write_text(
            'op.create_index("collide", "t", ["is_active"])\n',
            encoding="utf-8",
        )
        orm = {i.name: i for i in gate.parse_orm_indexes(models)}
        mig = {i.name: i for i in gate.parse_migration_indexes(migration)}
        assert not gate.same_definition(orm["collide"], mig["collide"])
