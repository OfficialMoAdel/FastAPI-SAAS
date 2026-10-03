"""``@cache`` endpoint decorator — caching + invalidation for FastAPI handlers."""

import functools
import secrets
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from pydantic import TypeAdapter

from src.app.core.exceptions.cache_exceptions import (
    InvalidRequestError,
    MissingClientError,
)
from src.app.core.utils import cache as _state

# P-M1: cache-key version. v2 = fail-closed write path (entries are only
# ever response-model-filtered bytes). Pre-v2 entries may hold unfiltered
# payloads and are never read again; they expire naturally via TTL.
_CACHE_KEY_VERSION = "v2"

# How long a refresh claim is honoured before Redis expires it. This is only
# a backstop for a worker that dies mid-refresh; no waiter reasons about it.
_REFRESH_CLAIM_TTL = 5
# The wait budget a NON-owner gives the owner before giving up and computing
# its own answer. Deliberately FAR smaller than the claim TTL: a waiter that
# waits out the claim has waited for an outcome that can no longer arrive
# (the owner either published long ago or already lost the claim), so the
# whole herd would recompute at t == claim-TTL — the stampede, merely
# delayed. Staying well under the TTL keeps slow-but-alive owners
# deduplicated, and makes the give-up path fan out in PARALLEL instead of
# firing in lockstep the moment the budget runs out.
_REFRESH_WAIT_TIMEOUT = 1.0
_REFRESH_WAIT_BASE_DELAY = 0.05
_REFRESH_WAIT_MAX_DELAY = 1.0
_REFRESH_WAIT_JITTER = 0.25


def _record_read(start_time: float, *, is_hit: bool) -> None:
    """Record one cache read into the shared XFetch hit/miss series.

    The decorator is the main producer of endpoint-cache traffic, so without
    this the ``xfetch_hit_rate`` on /api/metrics describes only the (nearly
    unused) ``get_with_stampede_protection`` reader and the endpoint cache
    looks either infinitely effective or entirely idle. Recorded once per
    read, on the OUTCOME: a request deduped onto another caller's value did
    not hit a warm key on its first read, but it did not recompute either.
    """
    _state.record_xfetch_latency(time.perf_counter() - start_time, is_hit=is_hit)


# Argument names that must never be mistaken for a resource id when
# inferring one (F21): pagination / sizing ints are not identities.
_NON_ID_ARG_NAMES = frozenset({"page", "limit", "offset", "per_page", "page_size", "size"})


def _resolve_user_id(kwargs: dict[str, Any]) -> Any:
    """Resolve the authorized user id from handler kwargs (F23).

    Prefers the explicit ``current_user`` kwarg; falls back to the
    ``RequestContext`` (``ctx``) principal — endpoints taking
    ``ctx: RequestContextDep`` have no ``current_user`` kwarg, and without
    this fallback ``user_scoped=True`` silently degraded to tenant-shared
    keys. Returns None when no principal is present.
    """
    cu = kwargs.get("current_user")
    if isinstance(cu, dict):
        user_id = cu.get("id")
        if user_id is not None:
            return user_id
    elif cu is not None:
        user_id = getattr(cu, "id", None)
        if user_id is not None:
            return user_id
    ctx = kwargs.get("ctx")
    if ctx is not None:
        user_id = getattr(ctx, "user_id", None)
        if user_id is not None:
            return user_id
    return None


def _scoped_invalidation_pattern(pattern: str, tenant_id: str | None, user_id: Any) -> str:
    """Scope a pattern-index entry exactly like the invalidation lookup (F2).

    Register and invalidate MUST compute the identical index key, or the
    O(1) lookup never fires. Mirrors the tenant/user namespacing of
    ``_build_cache_key`` (including the ``public:`` namespace).
    """
    if user_id is not None:
        pattern = f"user:{user_id}:{pattern}"
    if tenant_id is None:
        return f"public:{pattern}"
    return _state.build_tenant_key(pattern, tenant_id)


@functools.lru_cache(maxsize=256)
def _write_adapter(model: type, *, is_list: bool) -> TypeAdapter:
    """Memoized validation adapter for the cache write path (P2 single pass).

    Route response models are a fixed startup-time set, so the cache is
    bounded. ``validate_python`` + ``dump_python`` use the same core
    validation/serialization as the per-item
    ``model_validate(...).model_dump(...)`` calls they replace.
    """
    return TypeAdapter(list[model] if is_list else model)  # type: ignore[valid-type]


def _infer_resource_id(kwargs: dict[str, Any], resource_id_type: type | tuple[type, ...]) -> int | str:
    """Infer the resource ID from a dictionary of keyword arguments.

    Parameters
    ----------
    kwargs: Dict[str, Any]
        A dictionary of keyword arguments.
    resource_id_type: Union[type, Tuple[type, ...]]
        The expected type of the resource ID, which can be integer (int) or a string (str).

    Returns
    -------
    Union[None, int, str]
        The inferred resource ID. If it cannot be inferred or does not match the expected type, it returns None.

    Note
    ----
        - When `resource_id_type` is `int`, the function looks for an argument with the key 'id'.
        - When `resource_id_type` is `str`, it attempts to infer the resource ID as a string.
    """
    resource_id: int | str | None = None
    for arg_name, arg_value in kwargs.items():
        # F21: bools are ints, and pagination ints are not identities —
        # without these guards `page=2` could become the cache key.
        if isinstance(arg_value, bool):
            continue
        if arg_name in _NON_ID_ARG_NAMES:
            continue
        if isinstance(arg_value, resource_id_type):
            resource_id = arg_value
            break

    if resource_id is None:
        raise _state.CacheIdentificationInferenceError

    return resource_id


def _extract_data_inside_brackets(input_string: str) -> list[str]:
    """Extract data inside curly brackets from a given string using regular expressions.

    Parameters
    ----------
    input_string: str
        The input string in which to find data enclosed within curly brackets.

    Returns
    -------
    List[str]
        A list of strings containing the data found inside the curly brackets within the input string.

    Example
    -------
    >>> _extract_data_inside_brackets("The {quick} brown {fox} jumps over the {lazy} dog.")
    ['quick', 'fox', 'lazy']
    """
    # U5: Use pre-compiled pattern for better performance
    return _state._BRACKET_PATTERN.findall(input_string)


def _construct_data_dict(data_inside_brackets: list[str], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Construct a dictionary based on data inside brackets and keyword arguments."""
    data_dict = {}
    for key in data_inside_brackets:
        data_dict[key] = kwargs.get(key, f"_{key}_missing_")
    return data_dict


def _format_prefix(prefix: str, kwargs: dict[str, Any]) -> str:
    """Format a prefix using keyword arguments.

    Parameters
    ----------
    prefix: str
        A template string with placeholders in curly brackets.
    kwargs: Dict[str, Any]
        Keyword arguments used to fill the placeholders.

    Returns
    -------
    str: The formatted prefix.
    """
    data_inside_brackets = _state._extract_data_inside_brackets(prefix)
    data_dict = _construct_data_dict(data_inside_brackets, kwargs)
    formatted_prefix = prefix.format(**data_dict)
    return formatted_prefix


def _format_extra_data(to_invalidate_extra: dict[str, str], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Format extra data based on provided templates and keyword arguments.

    This function takes a dictionary of templates and their associated values and a dictionary of keyword arguments.
    It formats the templates with the corresponding values from the keyword arguments and returns a dictionary
    where keys are the formatted templates and values are the associated keyword argument values.

    Parameters
    ----------
    to_invalidate_extra: Dict[str, str]
        A dictionary where keys are templates and values are associated values.
    kwargs: Dict[str, Any]
        Keyword arguments used to fill the placeholders.

    Returns
    -------
        Dict[str, Any]: A dictionary of formatted templates to values.
    """
    formatted_extra = {}
    for prefix, id_template in to_invalidate_extra.items():
        formatted_prefix = _format_prefix(prefix, kwargs)
        # F22: fail loudly at request time with the template + missing name
        # instead of IndexError/KeyError deep in the invalidation path.
        names = _state._extract_data_inside_brackets(id_template)
        if not names:
            raise ValueError(
                f"to_invalidate_extra id template {id_template!r} must contain "
                "a {kwarg} placeholder naming the id argument"
            )
        id_name = names[0]
        if id_name not in kwargs:
            raise ValueError(
                f"to_invalidate_extra id {id_name!r} (from {id_template!r}) is not an argument of the decorated handler"
            )
        formatted_extra[formatted_prefix] = kwargs[id_name]

    return formatted_extra


async def _delete_keys_by_pattern(pattern: str) -> None:
    """Delete keys from Redis that match a given pattern using the SCAN command.

    NOTE (P1-12): no longer called from the cache-write invalidation path —
    kept for explicit/admin use only (plus its benchmark and unit tests).

    This function iteratively scans the Redis key space for keys that match a specific pattern
    and deletes them. It uses the SCAN command to efficiently find keys, which is more
    performance-friendly compared to the KEYS command, especially for large datasets.

    The function scans the key space in an iterative manner using a cursor-based approach.
    It retrieves a batch of keys matching the pattern on each iteration and deletes them
    until no matching keys are left.

    Parameters
    ----------
    pattern : str
        The pattern to match keys against. The pattern can include wildcards,
        such as '*' for matching any character sequence. Example: 'user:*'

    Notes
    -----
    - The SCAN command is used with a count of 1000 to retrieve keys in batches.
      This count can be adjusted based on the size of your dataset and Redis performance.

    - The function uses the delete command to remove keys in bulk. If the dataset
      is extremely large, consider implementing additional logic to handle bulk deletion
      more efficiently.

    - Be cautious with patterns that could match a large number of keys, as deleting
      many keys simultaneously can impact the performance of the Redis server.
    """
    client = _state.client
    if client is None:
        return

    cursor = 0
    iterations = 0
    while True:
        iterations += 1
        if iterations > _state.MAX_SCAN_ITERATIONS:
            _state.stampede_logger.warning(
                "SCAN iteration limit reached in _delete_keys_by_pattern",
                extra={"pattern": pattern, "iterations": _state.MAX_SCAN_ITERATIONS},
            )
            break

        cursor, keys = await client.scan(cursor, match=pattern, count=1000)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break


def _build_cache_key(
    key_prefix: str,
    resource_id: Any,
    kwargs: dict[str, Any],
    user_scoped: bool,
    handler_name: str | None = None,
) -> str:
    """Build the tenant + (optional) user scoped cache key for ``@cache``.

    The decorator resolves the cache BEFORE the handler runs, so the key MUST
    encode the authorized principal (tenant and, when ``user_scoped``, user)
    to prevent cross-tenant / cross-user cache leakage.
    """
    tenant = _state.get_current_tenant()
    # S-16 FIX: truthiness, not `is not None`. NO_TENANT is falsy by design,
    # and it is what TenantMiddleware binds for every request without an
    # X-Tenant-ID header. `tenant is not None` therefore produced
    # `tenant:00000000-0000-0000-0000-000000000000:v2:...` instead of the
    # `public:` namespace this branch exists to guarantee — a phantom tenant
    # whose entries are indexed into a shard no invalidation can name.
    tenant_id = str(tenant.tenant_id) if tenant else None

    user_id: Any = None
    if user_scoped:
        # F23: fall back to the RequestContext principal — handlers taking
        # `ctx` have no `current_user` kwarg.
        user_id = _resolve_user_id(kwargs)
        if user_id is None:
            # Run-3 finding cache.posts_superuser_hard_delete_invalidates_wrong_key:
            # silently degrading to a tenant-shared key made a user_scoped
            # invalidation target a key no reader can produce, so a superuser
            # hard-delete left the post readable from cache for the full TTL.
            # A handler that cannot name a principal cannot participate in
            # user-scoped caching; fail loudly so a future signature change
            # cannot reintroduce a silent no-op invalidation.
            raise ValueError(
                "user_scoped=True requires a `current_user` or `ctx` handler "
                "kwarg so the cache key can bind the authorized user; "
                f"{handler_name or 'the decorated handler'} supplies neither"
            )

    formatted_key_prefix = _format_prefix(key_prefix, kwargs)
    # P-M1 key version: entries written before the fail-closed write path
    # (pre-fix versions may hold UNFILTERED payloads cached on validation
    # failure) are never read again. Bump on any future write-format change.
    base_key = f"{_CACHE_KEY_VERSION}:{formatted_key_prefix}:{resource_id}"
    if user_id is not None:
        base_key = f"user:{user_id}:{base_key}"
    if tenant_id is None:
        # P0 FIX (code-review): missing tenant must never collapse onto a
        # bare shared key. Public/unauthenticated entries live under an
        # explicit `public:` namespace so they can never collide with
        # `tenant:{id}:...` entries if a context bug drops the tenant.
        return f"public:{base_key}"
    return _state.build_tenant_key(base_key, tenant_id)


def _refresh_lock_key(cache_key: str) -> str:
    """The per-key claim key that serialises concurrent refreshes."""
    return f"{cache_key}:refresh_lock"


async def _claim_refresh_lock(client: Any, lock_key: str, token: str) -> bool:
    """Take the refresh claim for a key whose value is already cached."""
    acquired = await client.set(lock_key, token, nx=True, ex=_REFRESH_CLAIM_TTL)
    return bool(acquired)


async def _await_refreshed_value(client: Any, cache_key: str) -> bytes | str | None:
    """Poll for the value that the refresh owner is publishing.

    Backoff mirrors ``stampede.get_with_lock`` (exponential, capped, jittered)
    so waiters spread out instead of re-reading in lockstep, and the whole
    wait stays inside the claim TTL. ``None`` means the owner never published
    — it failed, or its claim expired — and the caller computes its own value,
    which is slower but still correct.
    """
    elapsed = 0.0
    delay = _REFRESH_WAIT_BASE_DELAY
    while elapsed < _REFRESH_WAIT_TIMEOUT:
        delay = min(delay * 2, _REFRESH_WAIT_MAX_DELAY)
        actual = delay * (1 + _state.random.uniform(-_REFRESH_WAIT_JITTER, _REFRESH_WAIT_JITTER))
        await _state.asyncio.sleep(actual)
        elapsed += actual
        cached: bytes | str | None = await client.get(cache_key)
        if cached:
            return cached
    return None


async def _release_refresh_lock(client: Any, request: Request) -> None:
    """Release this request's refresh claim, but only if we still hold it.

    An unconditional ``DEL`` would drop a claim a slower worker took after
    ours expired, letting two handlers compute the same key at once. The
    compare-and-delete script is the ownership check; the claim TTL backstops
    a release that cannot run at all.
    """
    lock_key: str | None = getattr(request.state, "cache_refresh_lock_key", None)
    if not lock_key:
        return
    # Always a str: the claim is only ever written by this module, and a
    # missing token (a claim taken by an older caller) must not raise here.
    token = getattr(request.state, "cache_refresh_lock_token", "") or ""
    try:
        await _state.get_lua_manager().execute(
            client,
            "lock_release_if_owned",
            keys=[lock_key],
            args=[token],
        )
    except Exception:  # pragma: no cover - best effort; the claim TTL backstops it
        try:
            await client.delete(lock_key)
        except Exception:
            pass


async def _try_get_cached(
    client: Any,
    cache_key: str,
    request: Request,
    expiration: int,
) -> bytes | str | None:
    """P3-2: read-through cache path extracted from cache().inner.

    The read and the refresh claim happen in ONE Lua roundtrip, which is what
    makes this single-flight: a read followed by a separate claim lets N
    concurrent misses all observe an empty cache and all run the handler, and
    that stampede is exactly what this path exists to prevent. The claim is
    released by ``cache().inner`` once the owner has published.

    On script failure the read degrades to two GET+TTL calls and the caller
    serves a freshly computed value; a dead cache must never fail a request.
    A degraded MISS additionally returns None immediately rather than taking
    the waiter path: the claim is created by the same script that failed, so no
    owner exists to publish and waiting would only add the full wait budget of
    latency to a miss that is going to recompute anyway. A degraded HIT is still
    served from cache, and the probabilistic refresh-ahead still applies.

    P-M1: cached payloads are returned as stored — no Pydantic re-validation
    on the hit path. Every entry was already filtered through the route's
    response_model at WRITE time (cache().inner, "Filter against route
    response_model at write time"), so re-validating each item on every hit
    cost more CPU than the miss-path query it was meant to save. Validation
    failures on the write path fall back to serializing the raw result, so
    the cached bytes are always response-model-shaped.
    """
    lock_key = _refresh_lock_key(cache_key)
    token = secrets.token_hex(16)
    start_time = time.perf_counter()
    #: True when the atomic read+claim script failed and we fell back to plain
    #: GET+TTL. It matters that the CLAIM lives inside that script: with the
    #: script down nobody can own this key, so a miss has no owner to wait for.
    degraded = False
    try:
        result = await _state.get_lua_manager().execute(
            client,
            "xfetch_get_or_claim",
            keys=[cache_key, lock_key],
            args=[str(_REFRESH_CLAIM_TTL), token],
        )
        cached_data: bytes | str | None = result[0]
        remaining_ttl = int(result[1]) if result[1] is not None else -2
        claimed = bool(result[2]) if len(result) > 2 else False
    except Exception as e:
        # Degraded read: the claim is taken INSIDE the script that just
        # failed, so no caller can be an owner and no owner will ever
        # publish. The caller simply computes the value and the normal write
        # path caches it.
        _state.stampede_logger.warning(
            "Cache read script failed; serving fresh without caching",
            error=str(e),
            cache_key=cache_key,
        )
        cached_data = await client.get(cache_key)
        remaining_ttl = await client.ttl(cache_key) if cached_data else -2
        claimed = False
        degraded = True
    if not cached_data:
        if claimed:
            # We own this refresh: run the handler, publish, then release
            # the claim in cache().inner.
            _record_read(start_time, is_hit=False)
            request.state.cache_refresh_lock_key = lock_key
            request.state.cache_refresh_lock_token = token
            return None
        if degraded:
            # No owner exists and none ever will (the script that creates
            # owners is the one that failed), so polling here would burn the
            # whole wait budget on a value that cannot arrive — turning one
            # Redis-side fault into a latency stall on EVERY concurrent miss.
            # Compute now; the normal write path caches it (fail open).
            _record_read(start_time, is_hit=False)
            return None
        # Another request is already computing this key — wait for its value
        # rather than duplicating the work. Being served the owner's value is
        # a hit: this request never recomputed anything.
        refreshed = await _await_refreshed_value(client, cache_key)
        _record_read(start_time, is_hit=bool(refreshed))
        return refreshed

    if remaining_ttl > 0:
        delta = expiration - remaining_ttl
        if delta > 0:
            probability = min((delta / expiration) ** 2, 1.0)
            if _state.random.random() < probability:
                if await _claim_refresh_lock(client, lock_key, token):
                    _record_read(start_time, is_hit=False)
                    request.state.cache_refresh_lock_key = lock_key
                    request.state.cache_refresh_lock_token = token
                    return None  # cache miss — let the handler refresh

    _record_read(start_time, is_hit=True)
    return cached_data


def cache(  # noqa: C901
    key_prefix: str,
    resource_id_name: Any = None,
    expiration: int = 3600,
    resource_id_type: type | tuple[type, ...] = int,
    to_invalidate_extra: dict[str, Any] | None = None,
    pattern_to_invalidate_extra: list[str] | None = None,
    invalidation_patterns: list[str] | None = None,
    user_scoped: bool = False,
) -> Callable:
    """Cache decorator for FastAPI endpoints.

    This decorator enables caching the results of FastAPI endpoint functions to improve response times
    and reduce the load on the application by storing and retrieving data in a cache.

    Parameters
    ----------
    key_prefix: str
        A unique prefix to identify the cache key.
    resource_id_name: Any, optional
        The name of the resource ID argument in the decorated function. If provided, it is used directly;
        otherwise, the resource ID is inferred from the function's arguments.
    expiration: int, optional
        The expiration time for the cached data in seconds. Defaults to 3600 seconds (1 hour).
    resource_id_type: Union[type, Tuple[type, ...]], default int
        The expected type of the resource ID.
        This can be a single type (e.g., int) or a tuple of types (e.g., (int, str)).
        Defaults to int. This is used only if resource_id_name is not provided.
    to_invalidate_extra: Dict[str, Any] | None, optional
        A dictionary where keys are cache key prefixes and values are templates for cache key suffixes.
        These keys are invalidated when the decorated function is called with a method other than GET.
    pattern_to_invalidate_extra: List[str] | None, optional
        A list of string patterns for cache keys that should be invalidated when the decorated function is called.
        This allows for bulk invalidation of cache keys based on a matching pattern.
    invalidation_patterns: List[str] | None, optional
        A list of patterns to register this cache key against for O(1) invalidation.
        When this endpoint is called (GET), the cache key will be added to the index sets for these patterns.
        This allows future O(1) invalidation using `invalidate_by_pattern`.

    Notes
    -----
    Cached GET responses are returned as a raw `Response(content=...,
    media_type="application/json")`. FastAPI does NOT apply the route's
    `response_model` to these responses, so field filtering / `exclude` / `alias`
    rules declared on the endpoint are ignored for cache hits. Only decorate
    handlers that return response-model-shaped data (e.g. dicts produced via
    `schema_to_select`), never raw ORM objects.

    Returns
    -------
    Callable
        A decorator function that can be applied to FastAPI endpoint functions.

    Example usage
    -------------

    ```python
    from fastapi import FastAPI, Request
    from my_module import cache  # Replace with your actual module and imports

    app = FastAPI()

    # Define a sample endpoint with caching
    @app.get("/sample/{resource_id}")
    @cache(key_prefix="sample_data", expiration=3600, resource_id_type=int)
    async def sample_endpoint(request: Request, resource_id: int):
        # Your endpoint logic here
        return {"data": "your_data"}
    ```

    This decorator caches the response data of the endpoint function using a unique cache key.
    The cached data is retrieved for GET requests, and the cache is invalidated for other types of requests.

    Advanced Example Usage
    -------------
    ```python
    from fastapi import FastAPI, Request
    from my_module import cache

    app = FastAPI()


    @app.get("/users/{user_id}/items")
    @cache(key_prefix="user_items", resource_id_name="user_id", expiration=1200)
    async def read_user_items(request: Request, user_id: int):
        # Endpoint logic to fetch user's items
        return {"items": "user specific items"}


    @app.put("/items/{item_id}/data")
    @cache(
        key_prefix="item_data",
        resource_id_name="item_id",
        to_invalidate_extra={"user_items": "{user_id}"},
        pattern_to_invalidate_extra=["user_*_items:*"],
    )
    async def update_item(request: Request, item_id: int, data: dict, user_id: int):
        # Update logic to invalidate both the specific item cache and all user-specific item lists
        return {"status": "updated"}
    ```

    Note
    ----
    - resource_id_type is used only if resource_id is not passed.
    - `to_invalidate_extra` and `pattern_to_invalidate_extra` are used for cache invalidation on methods other than GET.
    - Using `pattern_to_invalidate_extra` can be resource-intensive on large datasets. Use it judiciously and
      consider the potential impact on Redis performance.
    """

    def wrapper(func: Callable) -> Callable:  # noqa: C901
        @functools.wraps(func)
        async def inner(request: Request, *args: Any, **kwargs: Any) -> Any:  # noqa: C901
            client = _state.client
            if client is None:
                raise MissingClientError

            if resource_id_name:
                resource_id = kwargs[resource_id_name]
            else:
                resource_id = _infer_resource_id(kwargs=kwargs, resource_id_type=resource_id_type)

            cache_key = _build_cache_key(
                key_prefix,
                resource_id,
                kwargs,
                user_scoped,
                getattr(func, "__qualname__", None),
            )

            if request.method == "GET":
                if to_invalidate_extra is not None or pattern_to_invalidate_extra is not None:
                    raise InvalidRequestError

                # P3-2: read-through path extracted to _try_get_cached.
                # P2-4: fail OPEN on cache-read failure (e.g. Redis down):
                # a freshly computed response is always correct, so serve
                # it instead of 500ing. The next request re-caches.
                try:
                    cached_data = await _try_get_cached(client, cache_key, request, expiration)
                except Exception as e:
                    _state.stampede_logger.warning(
                        "Cache read failed; serving fresh without caching",
                        error=str(e),
                        cache_key=cache_key,
                    )
                    cached_data = None
                if cached_data is not None:
                    # P6: Skip re-parse - return raw JSON bytes directly
                    return Response(content=cached_data, media_type="application/json")

            try:
                result = await func(request, *args, **kwargs)
            except Exception:
                # Release our refresh claim so waiters stop blocking on a
                # handler that will never publish.
                await _release_refresh_lock(client, request)
                raise
            if request.method == "GET":
                # F35: ONE try/finally owns the refresh-claim release for the
                # entire GET write path — including the fail-closed early
                # return below, which previously skipped the only release
                # site and left <key>:refresh_lock set for the full 5s TTL.
                # That leak is not cosmetic: the claim is what every other
                # caller blocks on, so a single leaking owner turns one bad
                # payload into a 5s stall for the whole herd, followed by a
                # simultaneous recompute that leaks again. Scoping the finally
                # to the whole block also means a future early return added
                # here cannot reintroduce the leak.
                try:
                    # Filter against route response_model at write time if present
                    to_serialize = result
                    route = request.scope.get("route") if hasattr(request, "scope") and request.scope else None
                    response_model = getattr(route, "response_model", None) if route else None
                    if response_model is not None:
                        try:
                            if hasattr(response_model, "model_validate"):
                                # P2 single pass: memoized adapter validates +
                                # dumps with the same model/mode/alias as the
                                # per-item calls it replaces, so cached bytes
                                # are identical; ValidationError still fails
                                # closed below.
                                adapter = _write_adapter(response_model, is_list=isinstance(result, list))
                                to_serialize = adapter.dump_python(
                                    adapter.validate_python(result), mode="json", by_alias=True
                                )
                        except Exception as e:
                            # P-M1 fail-closed: NEVER cache a payload that failed
                            # response-model filtering. Serve it fresh-only for
                            # this request; the next request re-runs the handler.
                            # (The old fallback serialized the raw result into
                            # the cache — combined with a no-revalidation hit
                            # path, unfiltered data would be served to everyone.)
                            _state.stampede_logger.warning(
                                "Cache write validation failed; serving fresh without caching",
                                error=str(e),
                                cache_key=cache_key,
                            )
                            return Response(content=_state.json_dumps(result), media_type="application/json")

                    # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
                    serialized_data = _state.json_dumps(to_serialize)
                    # P2-4: fail OPEN on cache-write failure (e.g. Redis down):
                    # serve the fresh payload without caching instead of 500ing.
                    try:
                        async with client.pipeline(transaction=False) as pipe:
                            pipe.setex(cache_key, expiration, serialized_data)

                            # Pattern-based invalidation registration (F2): index
                            # under the tenant/user-scoped pattern — identical to
                            # the lookup in the invalidation branch below — or the
                            # O(1) lookup never fires under tenant context.
                            if invalidation_patterns:
                                _reg_tenant = _state.get_current_tenant()
                                # S-13 FIX: truthiness, matching
                                # `_build_cache_key`. `NO_TENANT` is falsy by
                                # design, so `is not None` produced the
                                # UUID-zero id here while the DATA was written
                                # under `public:` — the pattern index was named
                                # after a tenant that owns none of those keys,
                                # and every scoped invalidation missed. All
                                # three seams in this module must agree.
                                _reg_tenant_id = str(_reg_tenant.tenant_id) if _reg_tenant else None
                                _reg_user_id = _resolve_user_id(kwargs) if user_scoped else None
                                formatted_patterns = [_format_prefix(p, kwargs) for p in invalidation_patterns]
                                for pattern in formatted_patterns:
                                    scoped = _scoped_invalidation_pattern(pattern, _reg_tenant_id, _reg_user_id)
                                    idx_key = _state._pattern_index_key(scoped)
                                    pipe.sadd(idx_key, cache_key)
                                    # P1-9: index outlives the data key (tenant-index
                                    # convention at the tenant branches below).
                                    pipe.expire(idx_key, expiration * 2)

                            # ISSUE 2 FIX: Register tenant-scoped keys for invalidate_tenant_cache()
                            if cache_key.startswith("tenant:"):
                                parts = cache_key.split(":", 3)
                                if len(parts) >= 2:
                                    tenant_id = parts[1]
                                    pipe.sadd(_state._tenant_index_key(tenant_id), cache_key)
                                    pipe.expire(_state._tenant_index_key(tenant_id), expiration * 2)

                                    # Also add to prefix-sharded index.
                                    # NOTE: shard by the business prefix (key_prefix), NOT parts[2] —
                                    # for user_scoped keys parts[2] is "user", which would make
                                    # invalidate_tenant_cache(tenant_id, prefix="...") find nothing.
                                    sharded_idx_key = _state._tenant_prefix_index_key(
                                        tenant_id, _format_prefix(key_prefix, kwargs)
                                    )
                                    pipe.sadd(sharded_idx_key, cache_key)
                                    pipe.expire(sharded_idx_key, expiration * 2)

                            await pipe.execute()
                    except Exception as e:
                        _state.stampede_logger.warning(
                            "Cache write failed; serving fresh without caching",
                            error=str(e),
                            cache_key=cache_key,
                        )

                    return Response(content=serialized_data, media_type="application/json")
                finally:
                    # Compare-and-delete: we never release a claim a slower
                    # worker now owns.
                    await _release_refresh_lock(client, request)

            else:
                # F3: extra keys go through the same key builder (tenant +
                # user + version namespacing) — a bare `{prefix}:{id}`
                # never matches a real entry.
                keys_to_delete = [cache_key]
                if to_invalidate_extra is not None:
                    formatted_extra = _format_extra_data(to_invalidate_extra, kwargs)
                    for prefix, id in formatted_extra.items():
                        keys_to_delete.append(
                            _build_cache_key(
                                prefix,
                                id,
                                kwargs,
                                user_scoped,
                                getattr(func, "__qualname__", None),
                            )
                        )

                if keys_to_delete:
                    # P2-4: fail OPEN — the mutation above already succeeded;
                    # a dead cache backend must not 500 the request.
                    # (Reads may be briefly stale; they revalidate on TTL.)
                    try:
                        async with client.pipeline() as pipe:
                            for key in keys_to_delete:
                                pipe.delete(key)
                            await pipe.execute()
                    except Exception as e:
                        _state.stampede_logger.warning(
                            "Cache invalidation failed; serving mutated result",
                            error=str(e),
                        )

                if pattern_to_invalidate_extra is not None:
                    # Recompute the authorized principal (mirrors _build_cache_key)
                    # so pattern invalidation never wipes another tenant's / user's keys.
                    _tenant = _state.get_current_tenant()
                    # S-13 FIX: truthiness, matching the registration side and
                    # `_build_cache_key`. Register and invalidate must compute
                    # the IDENTICAL index name or the O(1) lookup never fires.
                    _tenant_id = str(_tenant.tenant_id) if _tenant else None
                    _user_id: Any = _resolve_user_id(kwargs) if user_scoped else None

                    for pattern in pattern_to_invalidate_extra:
                        formatted_pattern = _format_prefix(pattern, kwargs)
                        # F2: same scoping function as the registration side.
                        scoped_pattern = _scoped_invalidation_pattern(formatted_pattern, _tenant_id, _user_id)

                        # P1-12: O(1) pattern-index only. The old SCAN fallback
                        # was O(N) over the whole keyspace on every write
                        # whose index was empty (e.g. a GET endpoint missing
                        # `invalidation_patterns`) — up to
                        # MAX_SCAN_ITERATIONS round-trips per mutating
                        # request. An empty index means no registered keys
                        # to delete; a recurring miss means the GET side is
                        # missing `invalidation_patterns`, so log it (same
                        # event name as before) and fix the registration
                        # instead of scanning. _delete_keys_by_pattern
                        # remains available for explicit/admin use but is no
                        # longer on the write hot path.
                        from src.app.core.utils.cache.patterns import invalidate_by_pattern as _invalidate_by_pattern

                        # P2-4: fail OPEN (see above).
                        try:
                            deleted = await _invalidate_by_pattern(scoped_pattern)
                        except Exception as e:
                            _state.stampede_logger.warning(
                                "cache_invalidation_scan_fallback",
                                extra={"pattern": scoped_pattern, "error": str(e)},
                            )
                            continue
                        if not deleted:
                            _state.stampede_logger.warning(
                                "cache_invalidation_scan_fallback",
                                extra={"pattern": scoped_pattern},
                            )

            return result

        return inner

    return wrapper
