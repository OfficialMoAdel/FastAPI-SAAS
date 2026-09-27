"""bound_tenant must restore the outer context, not clear it.

``unbind_tenant`` ends in ``clear_tenant_context``, which is
``ContextVar.set(None)``. ``ContextVar.set`` returns a token that
``reset`` needs to restore the previous value; ``set(None)`` discards it,
so a NESTED ``bound_tenant`` destroyed the outer binding on inner exit.

This only stayed invisible on the request path because each request runs
in a fresh task with no outer context to lose. The AI background task
(modules/ai/service.py:841) re-binds inside a retry loop and inherits its
context from the request, so it is exposed.

On the request path the previous value is None, so restoring is
identical to clearing — the tests below pin both halves.
"""

import pytest

from src.app.core.tenancy.context import (
    TenantContext,
    clear_tenant_context,
    get_current_tenant,
    set_current_tenant,
)
from src.app.core.tenancy.lifecycle import bound_tenant, unbind_tenant


def _ctx(tenant_id: str, slug: str) -> TenantContext:
    from uuid import UUID

    return TenantContext(
        tenant_id=UUID(tenant_id),
        tenant_slug=slug,
        tenant_name=slug,
        is_active=True,
    )


@pytest.fixture(autouse=True)
def _clean():
    clear_tenant_context()
    yield
    clear_tenant_context()


class TestNestedBoundTenant:
    def test_inner_exit_restores_the_outer_tenant(self) -> None:
        """Pre-fix this left the context as None after the inner block."""
        outer = _ctx("11111111-1111-1111-1111-111111111111", "outer")
        inner = _ctx("22222222-2222-2222-2222-222222222222", "inner")

        with bound_tenant(outer):
            assert get_current_tenant() is outer
            with bound_tenant(inner):
                assert get_current_tenant() is inner
            # The outer binding must survive the inner block.
            assert get_current_tenant() is outer, (
                "nested bound_tenant destroyed the outer context on inner exit"
            )
        assert get_current_tenant() is None

    def test_three_levels_of_nesting(self) -> None:
        a = _ctx("11111111-1111-1111-1111-111111111111", "a")
        b = _ctx("22222222-2222-2222-2222-222222222222", "b")
        c = _ctx("33333333-3333-3333-3333-333333333333", "c")

        with bound_tenant(a):
            with bound_tenant(b):
                with bound_tenant(c):
                    assert get_current_tenant() is c
                assert get_current_tenant() is b
            assert get_current_tenant() is a
        assert get_current_tenant() is None

    def test_retry_loop_rebinding_preserves_the_inherited_context(self) -> None:
        """The AI background-task shape: re-bind per attempt, inside a loop.

        With clear-on-exit, attempt 1's exit wipes the inherited request
        context and every later attempt runs unbound.
        """
        inherited = _ctx("11111111-1111-1111-1111-111111111111", "request")
        retried = _ctx("22222222-2222-2222-2222-222222222222", "retry")

        set_current_tenant(inherited)
        for _attempt in range(3):
            with bound_tenant(retried):
                assert get_current_tenant() is retried
            assert get_current_tenant() is inherited, (
                "the inherited request context was lost after the first retry"
            )


class TestRequestPathBehaviourIsUnchanged:
    def test_no_outer_context_is_cleared_on_exit(self) -> None:
        """The request path: nothing bound beforehand, so exit must be None."""
        tenant = _ctx("11111111-1111-1111-1111-111111111111", "acme")
        with bound_tenant(tenant):
            assert get_current_tenant() is tenant
        assert get_current_tenant() is None

    def test_explicit_none_binding(self) -> None:
        """bound_tenant(None) is how the middleware marks exempt paths."""
        with bound_tenant(None):
            assert get_current_tenant() is None
        assert get_current_tenant() is None

    def test_exception_inside_the_span_still_restores(self) -> None:
        outer = _ctx("11111111-1111-1111-1111-111111111111", "outer")

        with pytest.raises(RuntimeError):
            with bound_tenant(outer):
                raise RuntimeError("boom")

        assert get_current_tenant() is None, "a raising span must not leak its context"

    def test_unbind_tenant_still_clears(self) -> None:
        """unbind_tenant is the raw clear; the event worker relies on it."""
        set_current_tenant(_ctx("11111111-1111-1111-1111-111111111111", "a"))
        unbind_tenant()
        assert get_current_tenant() is None
