"""Runtime security configuration: one store, one key grammar, one apply path.

An operator who PATCHes ``/security/admin/config`` (or the auto-ban twin)
stores an override that has to outlive the process; boot reads it back and
injects it into ``settings.security``. Both halves used to be hand-rolled at
the call sites: two writers that each duplicated "mutate settings, then
persist", and a boot reader returning ``None`` both for "no override stored"
and for "Redis is down" — so an override lost to an outage looked exactly
like an operator who had never set one.

What this module owns:
* ``SECURITY_RUNTIME_KEYS`` — the key grammar. A key outside it is a typo and
  now fails loud instead of being stored as a key nothing will ever read.
* ``apply_and_store_runtime_config`` — the ONE writer: validate the keys,
  inject into ``settings.security``, merge into the store under its lock.
* ``load_runtime_config`` — the ONE boot reader.

The Redis key and TTL are unchanged from the previous store, so an override
an operator already stored keeps applying.

Boot failure policy: FAIL OPEN, LOUDLY. A Redis outage must not stop the
process from booting — the application already serves degraded (503) traffic
when Redis is unavailable, and the environment settings are a complete,
valid configuration on their own. What is not acceptable is failing
*silently*, so ``load_runtime_config`` logs the outage at error level and
applies nothing. The write path is best-effort for the same reason: an
operator's PATCH must succeed for this process even when the store is down.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter, ValidationError

from src.app.core.cache.client import cache_client
from src.app.core.config import settings
from src.app.core.json_utils import json_dumps, json_loads
from src.app.core.logger import get_logger

logger = get_logger(__name__)

# Preserved byte-for-byte: an override stored under the old key/TTL keeps
# applying. Changing either is a silent operator-data migration.
RUNTIME_CONFIG_KEY = "runtime_config:security"
RUNTIME_CONFIG_TTL = 86400 * 30  # 30 days

_LOCK_KEY = "lock:runtime_config"

# The key grammar. A write naming anything else is a programmer typo, not an
# override — see ``apply_and_store_runtime_config``.
SECURITY_RUNTIME_KEYS: tuple[str, ...] = (
    "IP_WHITELIST_ENABLED",
    "IP_BLACKLIST_ENABLED",
    "IP_WHITELIST_BYPASS_RATE_LIMIT",
    "AUTO_BAN_ENABLED",
    "AUTO_BAN_THRESHOLD",
    "AUTO_BAN_DURATION",
    "AUTO_BAN_ESCALATION",
)


class UnknownRuntimeConfigKeyError(ValueError):
    """A write named a key outside ``SECURITY_RUNTIME_KEYS``.

    Fail-loud counterpart to the boot reader's skip-and-log: a typo'd key used
    to be stored happily and then ignored forever at boot, so the operator saw
    a 200 and no effect.
    """


class RuntimeConfigUnavailableError(RuntimeError):
    """The stored overrides could not be read (Redis outage / undecodable payload).

    The signal that lets a caller tell "no override stored" (``{}``) from
    "could not ask" — the distinction the previous ``get_runtime_config() ->
    dict | None`` could not express.
    """


def _coerce_to_field_type(key: str, value: Any) -> Any:
    """Coerce one override to the annotated type of its ``settings.security`` field.

    ``key`` must already be a member of ``SECURITY_RUNTIME_KEYS``; that tuple
    is the single declaration of which fields are overridable.
    """
    field = type(settings.security).model_fields[key]
    raw = value.strip() if isinstance(value, str) else value
    return TypeAdapter(field.annotation).validate_python(raw)


def apply_runtime_config(config: Mapping[str, Any]) -> list[str]:
    """Inject stored overrides into ``settings.security``; return the applied keys.

    This is the ONE sanctioned site that mutates ``settings.security`` after
    boot. The stored blob is operator-writable state, so an unknown or invalid
    entry is logged and skipped rather than fatal — one bad key must not cost
    the process its other overrides.
    """
    applied: list[str] = []
    for key in SECURITY_RUNTIME_KEYS:
        if key not in config:
            continue
        try:
            setattr(settings.security, key, _coerce_to_field_type(key, config[key]))
        except (ValidationError, ValueError, TypeError) as e:
            logger.error("Skipping invalid security runtime config value", key=key, error=str(e))
            continue
        applied.append(key)
    return applied


async def read_stored_runtime_config() -> dict[str, Any]:
    """Return the stored overrides, or ``{}`` when none are stored.

    Raises:
        RuntimeConfigUnavailableError: Redis could not be asked, or the stored
            payload is not a decodable object. A missing cache client is a
            supported deployment (Redis cache disabled), not an outage, so it
            reads as "nothing stored" rather than an error.
    """
    if cache_client.client is None:
        return {}
    try:
        data = await cache_client.get(RUNTIME_CONFIG_KEY)
    except Exception as e:
        raise RuntimeConfigUnavailableError(str(e)) from e
    try:
        decoded = json_loads(data) if data else {}
    except Exception as e:
        raise RuntimeConfigUnavailableError(f"undecodable stored payload: {e}") from e
    if not isinstance(decoded, dict):
        raise RuntimeConfigUnavailableError(f"stored payload is {type(decoded).__name__}, not an object")
    return decoded


async def _write_stored_runtime_config(config: Mapping[str, Any]) -> bool:
    """Write the whole override blob back under the preserved key + TTL."""
    try:
        await cache_client.set(RUNTIME_CONFIG_KEY, json_dumps(config, as_str=True), ttl=RUNTIME_CONFIG_TTL)
        return True
    except Exception as e:
        logger.error("Failed to persist security runtime config", error=str(e))
        return False


async def _merge_stored_runtime_config(updates: Mapping[str, Any]) -> bool:
    """Merge ``updates`` into the stored blob under the store lock."""
    raw = cache_client.client
    if raw is None:
        logger.error("Security runtime config not persisted: no Redis client", keys=sorted(updates))
        return False
    try:
        async with raw.lock(_LOCK_KEY, timeout=10):
            current = await read_stored_runtime_config()
            current.update(updates)
            return await _write_stored_runtime_config(current)
    except Exception as e:
        logger.error("Failed to update security runtime config", error=str(e))
        return False


async def apply_and_store_runtime_config(updates: Mapping[str, Any]) -> None:
    """Validate, inject, and persist one admin PATCH — the ONE writer.

    Every key is checked against ``SECURITY_RUNTIME_KEYS`` and every value
    against its settings field annotation BEFORE anything is applied, so a
    rejected PATCH leaves both the process settings and the stored blob
    untouched. The in-process injection happens first (the PATCH's response
    is served from ``settings.security``); persistence is best-effort and
    logs its own failure.
    """
    validated: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in SECURITY_RUNTIME_KEYS:
            raise UnknownRuntimeConfigKeyError(key)
        try:
            validated[key] = _coerce_to_field_type(key, value)
        except (ValidationError, ValueError, TypeError) as e:
            raise ValueError(f"Invalid value for security runtime config key {key!r}") from e

    for key, value in validated.items():
        setattr(settings.security, key, value)

    await _merge_stored_runtime_config(validated)


async def load_runtime_config() -> list[str]:
    """Apply the stored overrides at boot; return the keys applied.

    Fail-open by decision (module docstring): a Redis outage is logged at
    error level and yields ``[]`` instead of stopping the process.
    """
    try:
        stored = await read_stored_runtime_config()
    except RuntimeConfigUnavailableError as e:
        logger.error(
            "Security runtime config unavailable at boot; continuing with environment settings",
            error=str(e),
        )
        return []
    if not stored:
        return []

    applied = apply_runtime_config(stored)
    logger.info("Security runtime config loaded from Redis", applied=applied)
    return applied
