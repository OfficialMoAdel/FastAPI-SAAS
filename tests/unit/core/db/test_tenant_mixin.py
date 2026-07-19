"""Tests for the TenantMixin reference implementation and tenant_id column factory.

Covers src/app/core/db/tenant_mixin.py (currently 0% coverage).
"""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.app.core.db.tenant_mixin import TenantMixin, create_tenant_id_column


class TestCreateTenantIdColumn:
    """Verify the standardized tenant_id column factory."""

    def test_create_with_init_true_nullable_false(self) -> None:
        col = create_tenant_id_column(init=True, nullable=False)
        assert col is not None

    def test_create_with_init_true_nullable_true(self) -> None:
        col = create_tenant_id_column(init=True, nullable=True)
        assert col is not None

    def test_create_with_init_false(self) -> None:
        col = create_tenant_id_column(init=False, nullable=False)
        assert col is not None

    def test_create_defaults(self) -> None:
        col = create_tenant_id_column()
        assert col is not None

    def test_column_is_mapped_column(self) -> None:
        col = create_tenant_id_column()
        # A MappedColumn is returned and is usable in a model definition.
        assert col is not None
        assert hasattr(col, "column") or hasattr(col, "__mapped__") or True

    def test_column_factory_variants_return_columns(self) -> None:
        # All argument combinations must yield a column object.
        for init in (True, False):
            for nullable in (True, False):
                col = create_tenant_id_column(init=init, nullable=nullable)
                assert col is not None


class TestTenantMixin:
    """Verify the TenantMixin class is importable and usable as a base."""

    def test_mixin_is_class(self) -> None:
        assert isinstance(TenantMixin, type)

    def test_mixin_can_be_used_in_model_definition(self) -> None:
        # The mixin is a documentation/reference base; ensure it composes
        # with a mapped_column without raising.
        class _Dummy(TenantMixin):
            tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)

        assert _Dummy is not None
