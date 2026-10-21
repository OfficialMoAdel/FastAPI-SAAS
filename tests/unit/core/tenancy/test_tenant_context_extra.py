"""``TenantContext.extra`` must be read-only.

The context is a frozen dataclass, but a plain dict inside it is still
mutable: ``ctx.extra["key"] = value`` bypasses the frozen check. The parsed
context is SHARED — the L1 memo hands the same instance to every request that
hits the cached tenant — so a mutation would leak into every other request's
view of that tenant.
"""

from uuid import uuid4

import pytest

from src.app.core.tenancy.cache import _L1_TENANT_PARSED
from src.app.core.tenancy.context import TenantContext
from src.app.core.tenancy.resolver import tenant_context_from_cache_data


def _context(**kwargs) -> TenantContext:
    return TenantContext(
        tenant_id=uuid4(),
        tenant_slug="acme",
        tenant_name="Acme",
        is_active=True,
        **kwargs,
    )


class TestExtraIsReadOnly:
    def test_extra_defaults_to_an_empty_mapping(self) -> None:
        assert dict(_context().extra) == {}

    def test_extra_cannot_be_mutated(self) -> None:
        context = _context()
        with pytest.raises(TypeError):
            context.extra["key"] = "value"  # type: ignore[index]

    def test_a_supplied_dict_is_copied_not_aliased(self) -> None:
        """The caller's dict is not the context's mapping."""
        source = {"a": 1}
        context = _context(extra=source)

        source["b"] = 2  # the caller's own dict is unaffected by the context

        assert dict(context.extra) == {"a": 1}
        with pytest.raises(TypeError):
            context.extra["c"] = 3  # type: ignore[index]

    def test_the_shared_parsed_context_cannot_be_mutated(self) -> None:
        """The memoised instance is shared between requests — and read-only."""
        _L1_TENANT_PARSED.clear()
        slug = "acme-shared"
        payload = {"id": str(uuid4()), "slug": slug, "name": "Acme", "is_active": True}

        first = tenant_context_from_cache_data(slug, payload)
        second = tenant_context_from_cache_data(slug, payload)

        assert first is second, "the parsed context is memoised per slug"
        with pytest.raises(TypeError):
            first.extra["x"] = 1  # type: ignore[index]
        _L1_TENANT_PARSED.clear()
