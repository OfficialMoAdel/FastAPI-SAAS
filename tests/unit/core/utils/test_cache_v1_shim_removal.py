"""Pin that the cache write path has no Pydantic v1 branch, and no payload drift.

Background: ``decorator.py`` used to carry an ``elif hasattr(response_model,
"parse_obj")`` fallback for v1-style route response models. It was deleted as
unreachable — see ``test_no_route_uses_a_v1_response_model`` for the proof.

Two properties are pinned here, and they are different in kind:

1. Reachability. Every ``response_model`` on every route is a v2 model. This
   is what makes the deleted branch dead rather than merely unused, and it is
   the assertion that fails if a v1 model is ever reintroduced.
2. Payload shape. A cache hit returns the same bytes as a cache miss. A
   serialisation change that altered either would break this.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel


def test_no_route_uses_a_v1_response_model() -> None:
    """No route declares a response_model that only has v1 methods.

    The v1 branch in the cache write path is guarded by
    ``not hasattr(response_model, "model_validate")``. If this test passes
    for every route, that guard can never be true and the branch is dead.

    Falsifiability: a route declared with a ``pydantic.v1`` model fails here.
    """
    from src.app.main import app

    v1_only: list[tuple[str, Any]] = []
    checked = 0

    for route in app.routes:
        response_model = getattr(route, "response_model", None)
        if response_model is None:
            continue
        checked += 1
        if hasattr(response_model, "model_validate"):
            continue
        if hasattr(response_model, "parse_obj"):
            v1_only.append((getattr(route, "path", repr(route)), response_model))

    assert not v1_only, f"routes with a v1-only response_model: {v1_only}"
    assert checked > 0, "no routes carried a response_model; the scan proved nothing"


async def test_cache_hit_bytes_match_cache_miss() -> None:
    """COUNTER-TEST: a cache hit returns byte-identical bytes to a miss.

    This is the assertion that catches a v1-to-v2 semantic difference.
    ``model_dump()`` excludes nothing by default whereas v1 ``dict()``
    excluded unset fields, so a slip in that direction would change the
    cached payload rather than raising.
    """
    from fastapi import Request

    from src.app.core.utils import cache as cache_mod

    class Out(BaseModel):
        name: str
        optional_unset: str | None = None

    stored: dict[bytes, bytes] = {}

    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=lambda key: stored.get(key))
    fake_client.ttl = AsyncMock(return_value=-1)
    fake_client.set = AsyncMock()
    fake_client.delete = AsyncMock()
    fake_client.sadd = AsyncMock()

    class _Pipe:
        def __init__(self) -> None:
            self.ops: list[tuple[bytes, bytes]] = []

        async def __aenter__(self) -> _Pipe:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        def setex(self, key: str, expiration: int, value: bytes) -> None:
            self.ops.append((key.encode(), value))

        def sadd(self, key: str, member: str) -> None:
            pass

    pipes: list[_Pipe] = []

    def _pipeline(*, transaction: bool) -> _Pipe:
        pipe = _Pipe()
        pipes.append(pipe)
        return pipe

    fake_client.pipeline = _pipeline

    def _make_request() -> Any:
        route = MagicMock()
        route.response_model = Out
        request = MagicMock(spec=Request)
        request.method = "GET"
        request.state = MagicMock()
        request.scope = {"route": route}
        return request

    @cache_mod.cache(key_prefix="v1shim", resource_id_name="rid")
    async def handler(request: Any, rid: int) -> Any:
        # An unset optional is the exact field v1 dict() would have dropped
        # and v2 model_dump() keeps. A drift shows up as differing bytes.
        return Out(name="alpha")

    with (
        patch.object(cache_mod, "client", fake_client),
        patch("src.app.core.utils.cache.get_current_tenant", return_value=None),
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        miss = await handler(_make_request(), rid=1)

    assert len(pipes) == 1, "expected exactly one cache write on a miss"
    _key, miss_bytes = pipes[0].ops[0]

    # Second call now reads the stored bytes instead of recomputing.
    stored[pipes[0].ops[0][0]] = miss_bytes
    with (
        patch.object(cache_mod, "client", fake_client),
        patch("src.app.core.utils.cache.get_current_tenant", return_value=None),
        patch("src.app.core.utils.cache.random.random", return_value=1.0),
    ):
        hit = await handler(_make_request(), rid=1)

    assert hit.body == miss.body, "cache hit payload drifted from cache miss payload"
    assert b"alpha" in hit.body


def test_names_banned_by_the_scan_cover_model_methods_only() -> None:
    """The scan's banned set must not include names shared with plain objects.

    `.dict()` on a model is a real v1 shim, but `.copy()` is dict/set/list and
    `.json()` is httpx/requests. A future contributor "helpfully" widening the
    gate to those would turn it red on correct code, and a gate that flags
    correct code gets switched off. This records why they stay out.
    """
    from scripts.checks.pydantic_v1_shims import V1_METHODS

    assert "dict" in V1_METHODS, ".dict() on a model is a real v1 shim"
    assert "parse_obj" in V1_METHODS
    assert "copy" not in V1_METHODS, ".copy() is dict/set/list; banning it flags correct code"
    assert "json" not in V1_METHODS, ".json() is httpx/requests; banning it flags correct code"
