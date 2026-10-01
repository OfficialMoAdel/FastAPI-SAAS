"""P2-1: a nested scope must survive an inner clear.

``clear_tenant_context`` used ``_current_tenant.set(None)`` and its docstring
promised to "prevent context leakage". That guarantee did not hold: ``set``
overwrites the current slot, it does not restore the previous value, so under
nesting an inner clear destroyed the OUTER binding.

The request path never showed it — each request is a fresh task with no outer
context to lose. The AI background task (modules/ai/service.py:841) does
re-bind inside a retry loop while inheriting its context from the request, so
it is the shape where the difference is observable.

``clear_tenant_context`` now returns the ``ContextVar`` token, and its
docstring says what it actually guarantees instead of promising restore.
"""

import pytest

from src.app.core.tenancy.context import (
    clear_tenant_context,
    get_current_tenant,
    set_current_tenant,
)
from src.app.core.tenancy.lifecycle import bound_tenant, preserved_tenant, unbind_tenant


def _ctx(name: str = "acme"):
    from uuid import uuid4

    from src.app.core.tenancy.context import TenantContext

    return TenantContext(
        tenant_id=uuid4(),
        tenant_slug=name,
        tenant_name=name.title(),
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _clean_context():
    """Every test starts and ends with no tenant bound."""
    clear_tenant_context()
    yield
    clear_tenant_context()


class TestNestedClearIsTheRegression:
    """The exact defect: an inner clear must not destroy the outer context."""

    def test_inner_clear_destroys_outer_with_plain_set(self) -> None:
        """Documents the old behaviour so the fix's value is legible.

        A raw ``set(None)`` inside an outer scope leaves the outer context
        gone — this is what the old function did.
        """
        from src.app.core.tenancy.context import _current_tenant

        outer = _ctx("outer")
        set_current_tenant(outer)
        try:
            _current_tenant.set(None)  # what clear_tenant_context used to be
            assert get_current_tenant() is None, (
                "expected the raw set to destroy the outer context — if this "
                "asserts the opposite, ContextVar semantics changed"
            )
        finally:
            set_current_tenant(outer)

    def test_clear_returns_a_usable_token(self) -> None:
        """The token lets a nested caller restore the outer context."""
        from src.app.core.tenancy.context import _current_tenant

        outer = _ctx("outer")
        set_current_tenant(outer)

        token = clear_tenant_context()
        assert get_current_tenant() is None, "clear must actually clear"

        _current_tenant.reset(token)
        assert get_current_tenant() is outer, "the token must restore the outer context"

    def test_bound_tenant_survives_an_inner_clear(self) -> None:
        """The real-world shape: a nested scope, cleared inside.

        ``bound_tenant`` restores on exit, so even if the body clears, the
        outer context is intact afterwards.
        """
        outer = _ctx("outer")
        inner = _ctx("inner")
        set_current_tenant(outer)

        with bound_tenant(inner):
            assert get_current_tenant() is inner
            clear_tenant_context()
            assert get_current_tenant() is None

        assert get_current_tenant() is outer, (
            "the outer tenant must survive an inner clear"
        )

    def test_preserved_tenant_survives_an_inner_clear(self) -> None:
        """The save/restore guard, same guarantee."""
        outer = _ctx("outer")
        set_current_tenant(outer)

        with preserved_tenant():
            set_current_tenant(_ctx("inner"))
            clear_tenant_context()

        assert get_current_tenant() is outer


class TestGenuineClearStillWorks:
    """COUNTER-TEST: top-level clearing is unchanged."""

    def test_clear_at_top_level_leaves_none(self) -> None:
        clear_tenant_context()
        assert get_current_tenant() is None

    def test_clear_with_nothing_bound_is_safe(self) -> None:
        """Clearing an already-empty context must not raise."""
        clear_tenant_context()
        clear_tenant_context()
        assert get_current_tenant() is None

    def test_unbind_clears_for_the_next_job(self) -> None:
        """The worker between-jobs case: clear, do not restore."""
        set_current_tenant(_ctx("job-1"))
        unbind_tenant()
        assert get_current_tenant() is None

    def test_bound_tenant_restores_none_at_top_level(self) -> None:
        """On the request path the previous value is None — restore == clear."""
        assert get_current_tenant() is None
        with bound_tenant(_ctx("req")):
            assert get_current_tenant() is not None
        assert get_current_tenant() is None


class TestDocsMatchBehaviour:
    """A docstring promising restore it does not provide is a real defect."""

    def test_clear_docstring_mentions_the_token(self) -> None:
        import inspect

        doc = inspect.getdoc(clear_tenant_context) or ""
        assert "token" in doc.lower(), (
            "clear_tenant_context must document the token it returns"
        )

    def test_unbind_docstring_says_it_clears_not_restores(self) -> None:
        import inspect

        doc = inspect.getdoc(unbind_tenant) or ""
        assert "clear" in doc.lower()
        assert "does not restore" in doc.lower(), (
            "unbind_tenant clears; the docstring must not imply restore"
        )

    def test_lifecycle_points_nested_callers_at_the_right_seam(self) -> None:
        import inspect

        from src.app.core.tenancy import lifecycle

        doc = inspect.getdoc(lifecycle.bound_tenant) or ""
        assert "restore" in doc.lower()
        # The AI retry-loop shape is the reason this exists; keep it named.
        assert "ai/service.py" in doc or "background task" in doc.lower(), (
            "bound_tenant should name the path that motivated it"
        )
