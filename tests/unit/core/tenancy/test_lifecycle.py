"""Unit tests for core/tenancy/lifecycle.py (A6 lifecycle seam).

Pins the ownership contract: one wire format both ends, leak-proof
bind/clear, snapshot restore, and job-context fabrication.
"""

from uuid import UUID

import pytest

from src.app.core.tenancy.context import (
    NO_TENANT,
    TenantContext,
    clear_tenant_context,
    get_current_tenant,
)
from src.app.core.tenancy.lifecycle import (
    TENANT_WIRE_KEYS,
    TenantWireError,
    bind_tenant,
    bound_tenant,
    fabricate_job_tenant,
    preserved_tenant,
    tenant_from_wire,
    tenant_to_wire,
    unbind_tenant,
)


@pytest.fixture(autouse=True)
def _clean_context():
    clear_tenant_context()
    yield
    clear_tenant_context()


def _ctx() -> TenantContext:
    return TenantContext(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
    )


class TestWireFormat:
    def test_keys_frozen(self) -> None:
        assert TENANT_WIRE_KEYS == ("id", "slug", "name", "is_active")

    def test_round_trip(self) -> None:
        wire = tenant_to_wire(_ctx())
        assert wire == {
            "id": "11111111-1111-1111-1111-111111111111",
            "slug": "acme",
            "name": "Acme",
            "is_active": True,
        }
        parsed = tenant_from_wire(wire)
        assert parsed == _ctx()

    def test_pack_none_and_sentinel(self) -> None:
        assert tenant_to_wire(None) is None
        assert tenant_to_wire(NO_TENANT) is None

    def test_parse_none(self) -> None:
        assert tenant_from_wire(None) is None

    def test_parse_missing_key(self) -> None:
        with pytest.raises(TenantWireError):
            tenant_from_wire({"id": "11111111-1111-1111-1111-111111111111"})

    def test_parse_null_value(self) -> None:
        wire = {"id": "11111111-1111-1111-1111-111111111111", "slug": None, "name": "x", "is_active": True}
        with pytest.raises(TenantWireError):
            tenant_from_wire(wire)

    def test_parse_bad_uuid(self) -> None:
        wire = {"id": "not-a-uuid", "slug": "s", "name": "n", "is_active": True}
        with pytest.raises(TenantWireError):
            tenant_from_wire(wire)

    def test_wire_error_is_value_error(self) -> None:
        # Worker DLQ branches catch broad Exception; ValueError lineage keeps
        # any ValueError-specific handling working too.
        assert issubclass(TenantWireError, ValueError)


class TestBindUnbind:
    def test_bind_sets(self) -> None:
        bind_tenant(_ctx())
        assert get_current_tenant() == _ctx()

    def test_unbind_clears(self) -> None:
        bind_tenant(_ctx())
        unbind_tenant()
        assert get_current_tenant() is None


class TestBoundTenant:
    def test_sets_and_clears(self) -> None:
        with bound_tenant(_ctx()) as bound:
            assert bound == _ctx()
            assert get_current_tenant() == _ctx()
        assert get_current_tenant() is None

    def test_clears_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            with bound_tenant(_ctx()):
                raise RuntimeError("boom")
        assert get_current_tenant() is None


class TestPreservedTenant:
    def test_restores_previous(self) -> None:
        bind_tenant(_ctx())
        other = TenantContext(
            tenant_id=UUID("22222222-2222-2222-2222-222222222222"),
            tenant_slug="other",
            tenant_name="Other",
            is_active=True,
        )
        with preserved_tenant() as prev:
            assert prev == _ctx()
            bind_tenant(other)
            assert get_current_tenant() == other
        assert get_current_tenant() == _ctx()

    def test_restores_none_previous(self) -> None:
        other = TenantContext(
            tenant_id=UUID("22222222-2222-2222-2222-222222222222"),
            tenant_slug="other",
            tenant_name="Other",
            is_active=True,
        )
        with preserved_tenant():
            bind_tenant(other)
        assert get_current_tenant() is None

    def test_restores_on_exception(self) -> None:
        bind_tenant(_ctx())
        with pytest.raises(RuntimeError):
            with preserved_tenant():
                bind_tenant(None)
                raise RuntimeError("boom")
        assert get_current_tenant() == _ctx()


class TestFabricateJobTenant:
    def test_values(self) -> None:
        fabricated = fabricate_job_tenant("11111111-1111-1111-1111-111111111111")
        assert fabricated.tenant_id == UUID("11111111-1111-1111-1111-111111111111")
        assert fabricated.tenant_slug == "job_context"
        assert fabricated.tenant_name == "Job Context"
        assert fabricated.is_active is True

    def test_uuid_passthrough(self) -> None:
        tid = UUID("11111111-1111-1111-1111-111111111111")
        assert fabricate_job_tenant(tid).tenant_id == tid
