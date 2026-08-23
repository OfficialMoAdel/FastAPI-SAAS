"""``@cache`` endpoint decorator — caching + invalidation for FastAPI handlers."""

import functools
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response

from src.app.core.exceptions.cache_exceptions import (
    InvalidRequestError,
    MissingClientError,
)
from src.app.core.utils import cache as _state


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
        id = _state._extract_data_inside_brackets(id_template)[0]
        formatted_extra[formatted_prefix] = kwargs[id]

    return formatted_extra


async def _delete_keys_by_pattern(pattern: str) -> None:
    """Delete keys from Redis that match a given pattern using the SCAN command.

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
        cu = kwargs.get("current_user")
        if isinstance(cu, dict):
            user_id = cu.get("id")
        elif cu is not None:
            user_id = getattr(cu, "id", None)

    formatted_key_prefix = _format_prefix(key_prefix, kwargs)
    base_key = f"{formatted_key_prefix}:{resource_id}"
    if user_id is not None:
        base_key = f"user:{user_id}:{base_key}"
    return _state.build_tenant_key(base_key, tenant_id)


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

                # Use Lua script for atomic GET+TTL (1 RT instead of 2)
                try:
                    lua_mgr = _state.get_lua_manager()
                    result = await lua_mgr.execute(
                        client,
                        "xfetch_get_with_ttl",
                        keys=[cache_key],
                        args=[],
                    )
                    cached_data = result[0]
                    remaining_ttl = int(result[1]) if result[1] is not None else -2
                    # Handle bytes → string
                    if cached_data is not None and isinstance(cached_data, bytes):
                        cached_data = cached_data.decode("utf-8")
                except Exception:
                    # Fallback: 2 separate calls
                    cached_data = await client.get(cache_key)
                    remaining_ttl = await client.ttl(cache_key) if cached_data else -2

                if cached_data:
                    if remaining_ttl > 0:
                        delta = expiration - remaining_ttl
                        if delta > 0:
                            probability = min((delta / expiration) ** 2, 1.0)
                            import random
                            if random.random() < probability:
                                lock_key = f"{cache_key}:refresh_lock"
                                lock_acquired = await client.set(lock_key, "1", nx=True, ex=5)
                                if lock_acquired:
                                    # We got the lock. Act as if cache miss to force synchronous refresh.
                                    cached_data = None
                                    request.state.cache_refresh_lock_key = lock_key

                    if cached_data is not None:
                        # H2 FIX: re-apply route response_model on cache hit so exclude/alias are not bypassed
                        try:
                            route = request.scope.get("route")  # type: ignore[attr-defined]
                            response_model = getattr(route, "response_model", None) if route else None
                            if response_model is not None:
                                import json as _json
                                raw = _json.loads(cached_data) if isinstance(cached_data, str | bytes) else cached_data
                                if hasattr(response_model, "model_validate"):
                                    validated = response_model.model_validate(raw)
                                    filtered = validated.model_dump(mode="json", by_alias=True)
                                else:
                                    validated = response_model.parse_obj(raw)
                                    filtered = validated.dict(by_alias=True)
                                if filtered is not None:
                                    import json as _j2
                                    cached_data = _j2.dumps(filtered, default=str)
                        except Exception:
                            pass
                        # P6: Skip re-parse - return raw JSON bytes directly
                        return Response(content=cached_data, media_type="application/json")

            result = await func(request, *args, **kwargs)

            if request.method == "GET":
                # U4: orjson handles datetime, UUID, Pydantic natively - skip jsonable_encoder
                serialized_data = _state.json_dumps(result)

                # Use pipeline for atomic set + index registration
                async with client.pipeline(transaction=False) as pipe:
                    pipe.setex(cache_key, expiration, serialized_data)

                    # Pattern-based invalidation registration
                    if invalidation_patterns:
                        formatted_patterns = [_format_prefix(p, kwargs) for p in invalidation_patterns]
                        for pattern in formatted_patterns:
                            idx_key = _state._pattern_index_key(pattern)
                            pipe.sadd(idx_key, cache_key)
                            pipe.expire(idx_key, expiration)

                    # ISSUE 2 FIX: Register tenant-scoped keys for invalidate_tenant_cache()
                    if cache_key.startswith("tenant:"):
                        parts = cache_key.split(":", 3)
                        if len(parts) >= 2:
                            tenant_id = parts[1]
                            pipe.sadd(_state._tenant_index_key(tenant_id), cache_key)
                            pipe.expire(_state._tenant_index_key(tenant_id), expiration * 2)

                            # Also add to prefix-sharded index
                            if len(parts) >= 3:
                                prefix = parts[2]
                                sharded_idx_key = _state._tenant_prefix_index_key(tenant_id, prefix)
                                pipe.sadd(sharded_idx_key, cache_key)
                                pipe.expire(sharded_idx_key, expiration * 2)

                    # Release refresh lock if we acquired it
                    refresh_lock_key = getattr(request.state, "cache_refresh_lock_key", None)
                    if refresh_lock_key:
                        pipe.delete(refresh_lock_key)

                    await pipe.execute()

                return Response(content=serialized_data, media_type="application/json")

            else:
                # OPT-6: Pipeline cache invalidation (N deletes → 1 roundtrip)
                keys_to_delete = [cache_key]
                if to_invalidate_extra is not None:
                    formatted_extra = _format_extra_data(to_invalidate_extra, kwargs)
                    for prefix, id in formatted_extra.items():
                        keys_to_delete.append(f"{prefix}:{id}")

                if keys_to_delete:
                    async with client.pipeline() as pipe:
                        for key in keys_to_delete:
                            pipe.delete(key)
                        await pipe.execute()

                if pattern_to_invalidate_extra is not None:
                    # Recompute the authorized principal (mirrors _build_cache_key)
                    # so pattern invalidation never wipes another tenant's / user's keys.
                    _tenant = _state.get_current_tenant()
                    _tenant_id = str(_tenant.tenant_id) if _tenant is not None else None
                    _user_id: Any = None
                    if user_scoped:
                        _cu = kwargs.get("current_user")
                        if isinstance(_cu, dict):
                            _user_id = _cu.get("id")
                        elif _cu is not None:
                            _user_id = getattr(_cu, "id", None)

                    for pattern in pattern_to_invalidate_extra:
                        formatted_pattern = _format_prefix(pattern, kwargs)
                        if _user_id is not None:
                            formatted_pattern = f"user:{_user_id}:{formatted_pattern}"
                        scoped_pattern = _state.build_tenant_key(formatted_pattern, _tenant_id)
                        # SCAN-based deletion: the O(1) index is not populated for
                        # these keys, so fall back to pattern matching. The pattern
                        # is tenant/user scoped to avoid wiping other tenants' data.
                        await _delete_keys_by_pattern(scoped_pattern)

            return result

        return inner

    return wrapper
