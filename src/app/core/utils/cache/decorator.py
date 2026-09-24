"""``@cache`` endpoint decorator — caching + invalidation for FastAPI handlers."""

import functools
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
                f"to_invalidate_extra id {id_name!r} (from {id_template!r}) "
                "is not an argument of the decorated handler"
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
) -> str:
    """Build the tenant + (optional) user scoped cache key for ``@cache``.

    The decorator resolves the cache BEFORE the handler runs, so the key MUST
    encode the authorized principal (tenant and, when ``user_scoped``, user)
    to prevent cross-tenant / cross-user cache leakage.
    """
    tenant = _state.get_current_tenant()
    tenant_id = str(tenant.tenant_id) if tenant is not None else None

    user_id: Any = None
    if user_scoped:
        # F23: fall back to the RequestContext principal — handlers taking
        # `ctx` have no `current_user` kwarg.
        user_id = _resolve_user_id(kwargs)

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


async def _try_get_cached(
    client: Any,
    cache_key: str,
    request: Request,
    expiration: int,
) -> bytes | str | None:
    """P3-2: read-through cache path extracted from cache().inner.

    Tries the Lua script first (1 RT), falls back to two GET+TTL calls
    on failure, then evaluates the probabilistic refresh-ahead. Returns
    the bytes to send in a Response, or None to indicate a cache miss.

    P-M1: cached payloads are returned as stored — no Pydantic re-validation
    on the hit path. Every entry was already filtered through the route's
    response_model at WRITE time (cache().inner, "Filter against route
    response_model at write time"), so re-validating each item on every hit
    cost more CPU than the miss-path query it was meant to save. Validation
    failures on the write path fall back to serializing the raw result, so
    the cached bytes are always response-model-shaped.
    """
    try:
        lua_mgr = _state.get_lua_manager()
        result = await lua_mgr.execute(
            client,
            "xfetch_get_with_ttl",
            keys=[cache_key],
            args=[],
        )
        cached_data: bytes | str | None = result[0]
        remaining_ttl = int(result[1]) if result[1] is not None else -2
    except Exception:
        cached_data = await client.get(cache_key)
        remaining_ttl = await client.ttl(cache_key) if cached_data else -2

    if not cached_data:
        return None

    if remaining_ttl > 0:
        delta = expiration - remaining_ttl
        if delta > 0:
            probability = min((delta / expiration) ** 2, 1.0)
            if _state.random.random() < probability:
                lock_key = f"{cache_key}:refresh_lock"
                lock_acquired = await client.set(lock_key, "1", nx=True, ex=5)
                if lock_acquired:
                    request.state.cache_refresh_lock_key = lock_key
                    return None  # cache miss — let the handler refresh

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

            cache_key = _build_cache_key(key_prefix, resource_id, kwargs, user_scoped)

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
                refresh_lock_key = getattr(request.state, "cache_refresh_lock_key", None)
                if refresh_lock_key and client is not None:
                    try:
                        await client.delete(refresh_lock_key)
                    except Exception:
                        pass
                raise
            if request.method == "GET":
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
                        elif hasattr(response_model, "parse_obj"):
                            if isinstance(result, list):
                                to_serialize = [response_model.parse_obj(item).dict(by_alias=True) for item in result]
                            else:
                                to_serialize = response_model.parse_obj(result).dict(by_alias=True)
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
                            _reg_tenant_id = str(_reg_tenant.tenant_id) if _reg_tenant is not None else None
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
                finally:
                    # F35: one path owns the refresh-lock release, so a
                    # failing pipeline no longer leaks it to the 5s expiry.
                    refresh_lock_key = getattr(request.state, "cache_refresh_lock_key", None)
                    if refresh_lock_key:
                        try:
                            await client.delete(refresh_lock_key)
                        except Exception:
                            pass

                return Response(content=serialized_data, media_type="application/json")

            else:
                # F3: extra keys go through the same key builder (tenant +
                # user + version namespacing) — a bare `{prefix}:{id}`
                # never matches a real entry.
                keys_to_delete = [cache_key]
                if to_invalidate_extra is not None:
                    formatted_extra = _format_extra_data(to_invalidate_extra, kwargs)
                    for prefix, id in formatted_extra.items():
                        keys_to_delete.append(_build_cache_key(prefix, id, kwargs, user_scoped))

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
                    _tenant_id = str(_tenant.tenant_id) if _tenant is not None else None
                    _user_id: Any = _resolve_user_id(kwargs) if user_scoped else None

                    for pattern in pattern_to_invalidate_extra:
                        formatted_pattern = _format_prefix(pattern, kwargs)
                        # F2: same scoping function as the registration side.
                        scoped_pattern = _scoped_invalidation_pattern(
                            formatted_pattern, _tenant_id, _user_id
                        )

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
