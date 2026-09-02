"""High-performance JSON utilities using orjson.

This module provides optimized JSON serialization/deserialization
with support for common Python types (UUID, datetime, Decimal, Pydantic models).

Usage:
    from src.app.core.json_utils import json_dumps, json_loads, orjson_dumps_default

    # Serialize to bytes (fastest)
    data_bytes = json_dumps({"key": "value"})

    # Serialize to string
    data_str = json_dumps({"key": "value"}, as_str=True)

    # Deserialize
    data = json_loads(data_bytes)
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, overload
from uuid import UUID

import orjson
from pydantic import BaseModel


def orjson_dumps_default(obj: Any) -> Any:
    """Default serializer for types not natively supported by orjson.

    Handles:
    - UUID -> string
    - Decimal -> float
    - datetime/date -> ISO format string
    - Pydantic models -> dict
    - Sets -> list

    Parameters
    ----------
    obj : Any
        Object to serialize

    Returns
    -------
    Any
        Serializable representation

    Raises
    ------
    TypeError
        If object type is not supported
    """
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="python")
    if isinstance(obj, set):
        return list(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


@overload
def json_dumps(
    obj: Any,
    *,
    as_str: Literal[True],
    default: Any | None = ...,
    option: int | None = ...,
) -> str: ...


@overload
def json_dumps(
    obj: Any,
    *,
    as_str: Literal[False] = ...,
    default: Any | None = ...,
    option: int | None = ...,
) -> bytes: ...


def json_dumps(
    obj: Any,
    *,
    as_str: bool = False,
    default: Any | None = None,
    option: int | None = None,
) -> bytes | str:
    """Serialize object to JSON bytes or string using orjson.

    Args:
        obj: The object to serialize
        as_str: If True, returns str instead of bytes
        default: Optional fallback serializer callable (defaults to orjson_dumps_default)
        option: Optional orjson options bitmask (defaults to OPT_UTC_Z when None)

    Returns:
        bytes (default) or str (if as_str=True)
    """
    default_fn = default or orjson_dumps_default
    if option is not None:
        result = orjson.dumps(obj, default=default_fn, option=option)
    else:
        result = orjson.dumps(obj, default=default_fn, option=orjson.OPT_UTC_Z)
    return result.decode("utf-8") if as_str else result


def json_loads(data: bytes | str | memoryview) -> Any:
    """Deserialize JSON bytes/string to Python object using orjson.

    Parameters
    ----------
    data : bytes | str | memoryview
        JSON data to parse

    Returns
    -------
    Any
        Parsed Python object
    """
    return orjson.loads(data)


def json_dumps_sorted(obj: Any) -> bytes:
    """Deterministic JSON serialization for cryptographic signatures.

    This function ensures consistent key ordering across Python versions
    and serialization libraries. MUST be used for any HMAC/signature
    generation to ensure reproducible signatures.

    Parameters
    ----------
    obj : Any
        Object to serialize

    Returns
    -------
    bytes
        JSON bytes with sorted keys (deterministic)

    Examples
    --------
    >>> payload = {"z": 1, "a": 2}
    >>> sig1 = hmac.new(secret, json_dumps_sorted(payload), "sha256")
    >>> sig2 = hmac.new(secret, json_dumps_sorted(payload), "sha256")
    >>> assert sig1.hexdigest() == sig2.hexdigest()  # Always True
    """
    return orjson.dumps(
        obj,
        default=orjson_dumps_default,
        option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_UTC_Z | orjson.OPT_SORT_KEYS,
    )


# Re-export orjson constants for advanced usage
OPT_INDENT_2 = orjson.OPT_INDENT_2
OPT_SORT_KEYS = orjson.OPT_SORT_KEYS
OPT_APPEND_NEWLINE = orjson.OPT_APPEND_NEWLINE
