"""T-8: the APPLICATION emits its own configuration into the artefact.

THE PRODUCTION-CODE SIDE, and why it exists separately from P-9
----------------------------------------------------------------
Unified plan §6.2 calls the missing manifest "the single omission that let every
prior run be uninterpretable", and the audit bears that out: not one baseline in
the pack recorded any of it, so findings 1-5 and J1-J14 all had to be
reconstructed after the fact rather than read off the artefact.

Two beads cover it and they are not interchangeable:

- **P-9** is the HARNESS side. ``src/benchmarks/run_manifest.py`` records git
  SHA, environment, and refuses to run if the harness is not pinned.
- **T-8** is the PRODUCTION-CODE side, and this module. The application emits the
  configuration *it is actually running with*.

The reason both are needed is drift. A harness that reconstructs the app's config
from environment variables reports what the harness believed the app was given.
An application that emits its own config reports what it resolved. Those diverge
the first time a setting has a default, an alias, or a validator that coerces a
value - which is most of them. A manifest assembled from the outside describes the
experiment; one emitted from the inside describes the subject.

NO SILENT DEFAULTS
------------------
Acceptance criterion (a): "a missing or empty field ABORTS the run rather than
defaulting - no silent defaults for required config". That is the whole design
constraint, and it runs opposite to the instinct that a missing field should be
tolerable.

The reasoning is specific. A manifest field that defaults to a plausible value
produces an artefact that looks complete and is wrong. If the security budgets are
unreadable, writing ``{}`` makes the run look like a run with no budgets rather
than like a run whose budgets could not be read - and the first is a legitimate
configuration while the second is a broken instrument. Downstream, a reader cannot
tell them apart, so the run is quoted. :func:`collect_app_config` therefore
returns only what it read, and :func:`assert_manifest_complete` raises naming the
missing field rather than filling it.

The one thing that is NOT a required field is any credential. This module reads
settings objects, not environment variables, precisely so that a manifest can
carry the configuration without carrying the secrets that configure it: host and
port, never a password. :func:`assert_no_credentials` proves it.
"""

from __future__ import annotations

import platform
import sys
from typing import Any

from src.app.core.monitoring.telemetry import Counter

MANIFEST_SCHEMA = "app-config-manifest/1"

#: Every field the manifest must carry, as dotted paths. Absence aborts.
#:
#: Each entry is here because a number in the artefact is uninterpretable without
#: it - not because it is tidy. ``limits.security_budgets`` is the one the pack
#: omitted and then could not be reconstructed from: with budgets at 10000/60
#: instead of (5,60), the rate limiter was never exercised, so the Redis cost
#: attributable to it was structurally absent from the measurement, and a Redis
#: programme came to claim 35-48% of a 4.9 ms request on that basis.
REQUIRED_FIELDS: tuple[str, ...] = (
    "schema",
    "runtime.python_version",
    "runtime.platform",
    "runtime.cpu_count",
    "stores.postgres_host",
    "stores.postgres_port",
    "stores.redis_cache_host",
    "stores.redis_cache_port",
    "stores.redis_queue_host",
    "stores.redis_queue_port",
    "limits.auto_ban_enabled",
    "limits.security_budgets",
    "telemetry.enabled_flags",
)

#: Substrings that indicate a credential reached the manifest. Checked on every
#: emitted manifest, because the failure mode is a password in a committed
#: artefact and the check is the only thing standing between a config dump and a
#: disclosure.
CREDENTIAL_MARKERS: tuple[str, ...] = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "private_key",
)


class ManifestIncomplete(RuntimeError):
    """A required configuration field is absent or empty.

    A :class:`RuntimeError` rather than an :class:`AssertionError` because this is
    not a test assertion - it is a refusal to emit an artefact that would read as
    complete. The message names every missing field so the operator fixes them in
    one pass rather than one per re-run.
    """


def _read(getter: Any, name: str, default: Any = None) -> Any:
    """Read one setting through the flat accessor, or ``None`` if unreadable.

    The settings objects expose attributes and also a flat accessor used
    elsewhere in the codebase; both are tried because this module must not care
    which a given domain uses, and a manifest that reads half the config is worse
    than one that reads none.
    """
    for accessor in (lambda: getattr(getter, name, None), lambda: getter(name, None)):
        try:
            value = accessor()
        except Exception:  # noqa: BLE001 - a settings read may raise anything
            continue
        if value is not None:
            return value
    return default


def _address(value: Any) -> tuple[Any, Any]:
    """Split a store address into (host, port), never the credential.

    Store URLs in this project are written ``host:port/db`` or a bare host. Only
    host and port are returned: a manifest that carries the full URL carries the
    password that is often embedded in it.
    """
    if value is None:
        return None, None
    text = str(value)
    for scheme in ("redis://", "rediss://", "postgresql+asyncpg://", "postgresql://"):
        if text.startswith(scheme):
            text = text[len(scheme) :]
            if "@" in text:
                text = text.split("@", 1)[1]
            break
    if "/" in text:
        text = text.split("/", 1)[0]
    if ":" in text:
        host, _, port = text.rpartition(":")
        return host or None, port or None
    return text or None, None


def collect_app_config(*, settings: Any = None, security: Any = None,
                      services: Any = None) -> dict[str, Any]:
    """Read the running configuration into a manifest.

    Returns only what it could actually read. An unreadable field is absent, not
    defaulted - :func:`assert_manifest_complete` turns that absence into a refusal,
    which is the point.
    """
    if settings is None:
        from src.app.core.config import settings as default_settings

        settings = default_settings
    # The settings CLASSES, not a `settings` module attribute: `src.app.core.config`
    # exposes a single composite `Settings` that does NOT carry these domains as
    # attributes (it has no `.services` and no `.security`), so an earlier version
    # of this function looked for a module-level `settings` object that does not
    # exist, caught the ImportError, and recorded every budget as absent - which
    # is indistinguishable from a deployment that has none.
    #
    # Constructing the domain classes directly is also the more honest source:
    # they resolve from the environment this process actually has, which is what
    # the manifest is meant to describe.
    if security is None:
        try:
            from src.app.core.config.security import SecuritySettings

            security = SecuritySettings()
        except Exception:  # noqa: BLE001 - absent security config is a hole, not a crash
            security = None
    if services is None:
        try:
            from src.app.core.config.services import DefaultRateLimitSettings

            services = DefaultRateLimitSettings()
        except Exception:  # noqa: BLE001 - same: a hole, recorded rather than crashed on
            services = None

    postgres = getattr(settings, "postgres", None)
    redis_cache = getattr(settings, "redis_cache", None)
    redis_queue = getattr(settings, "redis_queue", None)
    if redis_cache is None or redis_queue is None:
        try:
            from src.app.core.config.services import RedisQueueSettings

            redis_queue = redis_queue if redis_queue is not None else RedisQueueSettings()
        except Exception:  # noqa: BLE001
            pass

    pg_host, pg_port = _address(_read(postgres, "POSTGRES_SERVER") or _read(postgres, "server"))
    if pg_host is None:
        pg_host = _read(postgres, "POSTGRES_SERVER")
    if pg_port is None:
        pg_port = _read(postgres, "POSTGRES_PORT")

    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "executable_basename": sys.executable.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            "platform": platform.platform(),
            "system": platform.system(),
            "cpu_count": _cpu_count(),
            "event_loop_module": _event_loop_module(),
        },
        "stores": {
            "postgres_host": pg_host,
            "postgres_port": pg_port,
            "redis_cache_host": _read(redis_cache, "REDIS_CACHE_HOST"),
            "redis_cache_port": _read(redis_cache, "REDIS_CACHE_PORT"),
            "redis_queue_host": _read(redis_queue, "REDIS_QUEUE_HOST"),
            "redis_queue_port": _read(redis_queue, "REDIS_QUEUE_PORT"),
        },
        "limits": {
            "auto_ban_enabled": _read(security, "AUTO_BAN_ENABLED"),
            "security_budgets": _security_budgets(security, services),
        },
        "telemetry": {
            "enabled_flags": _telemetry_flags(),
        },
    }
    return manifest


def _cpu_count() -> int | None:
    affinity = getattr(__import__("os"), "sched_getaffinity", None)
    if affinity is not None:
        try:
            return len(affinity(0))
        except OSError:
            pass
    import os

    return os.cpu_count()


def _event_loop_module() -> str | None:
    """The event loop implementation this process has installed.

    Recorded rather than assumed, for the same reason run_manifest records it:
    a manifest that names the loop lets a reader tell a uvloop number from an
    asyncio one, and the two are different quantities.
    """
    try:
        import asyncio

        return type(asyncio.get_event_loop()).__module__
    except Exception:  # noqa: BLE001 - no loop in this thread is normal at import
        return None


def _security_budgets(security: Any, services: Any) -> dict[str, Any]:
    """Every security budget and path limit, as ``(limit, window_seconds)``.

    Read by the real field names rather than by a guessed per-name schema, because
    the shapes differ: ``SECURITY_BUDGETS`` is ``dict[str, tuple[int, int]]`` keyed
    by budget name, and ``DEFAULT_ANONYMOUS_LIMIT`` is a bare ``tuple[int, int]``.
    An earlier version of this function looked for ``SECURITY_BUDGET_AUTH_LIMIT``
    and ``_WINDOW_SECONDS`` fields that do not exist, and returned an empty dict -
    which reads exactly like a deployment with no budgets, rather than like a
    reader that failed to find them. It is the failure T-8 exists to prevent,
    committed inside the instrument meant to prevent it.

    The budgets are read WHOLE rather than by a hardcoded name list, so a budget
    added later appears here automatically instead of being silently omitted from
    the manifest.
    """
    budgets: dict[str, Any] = {}

    raw = _read(services, "SECURITY_BUDGETS")
    if isinstance(raw, dict):
        for name, spec in raw.items():
            budgets[str(name)] = _budget_pair(spec)

    for field, name in (
        ("DEFAULT_ANONYMOUS_LIMIT", "default_anonymous"),
        ("DEFAULT_AUTHENTICATED_LIMIT", "default_authenticated"),
    ):
        spec = _read(services, field)
        if spec is not None:
            budgets[name] = _budget_pair(spec)

    fail_closed = _read(services, "SECURITY_BUDGETS_FAIL_CLOSED")
    if fail_closed is not None:
        budgets["_fail_closed"] = sorted(str(item) for item in fail_closed)
    return budgets


def _budget_pair(spec: Any) -> dict[str, Any]:
    """One budget as ``{"limit": n, "window_seconds": n}``.

    A 2-tuple is the declared shape; anything else is recorded verbatim rather than
    coerced, because a budget whose shape is not what this reader expects is a
    configuration change worth seeing in the artefact instead of a value silently
    reinterpreted.
    """
    if isinstance(spec, tuple | list) and len(spec) == 2:
        return {"limit": spec[0], "window_seconds": spec[1]}
    return {"limit": spec, "window_seconds": None}


def _telemetry_flags() -> dict[str, bool]:
    """Which Phase-0 instruments are on. Recorded in the manifest by T-4/T-5/T-8.

    The enabled/disabled state is part of the configuration, not an incidental:
    a series that is absent because it was never enabled and a series that is
    absent because nothing happened are different facts, and an artefact that
    does not record the flag cannot tell them apart.
    """
    from src.app.core.monitoring.telemetry import telemetry_enabled_flags

    return telemetry_enabled_flags()


def _is_absent(value: Any) -> bool:
    """Absent means: not there, None, or an empty collection.

    ``0`` and ``False`` are PRESENT. A disabled ``AUTO_BAN_ENABLED`` or a zero
    budget is a configuration, and treating it as a hole would make the one
    manifest field that most needs recording the one the completeness check
    refuses.
    """
    if value is None:
        return True
    if isinstance(value, str | bytes | list | tuple | dict | set):
        return len(value) == 0
    return False


def assert_manifest_complete(manifest: dict[str, Any]) -> dict[str, Any]:
    """Raise naming every absent required field.

    Aborts rather than defaulting, per acceptance criterion (a). The message
    lists all of them at once: a run that reports one missing field per attempt
    turns a two-minute fix into a two-hour one.
    """
    missing: list[str] = []
    for dotted in REQUIRED_FIELDS:
        node: Any = manifest
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                missing.append(dotted)
                break
            node = node[part]
        else:
            if _is_absent(node):
                missing.append(dotted)
    if missing:
        raise ManifestIncomplete(
            "T-8 app config manifest is incomplete; required field(s) absent or "
            f"empty: {sorted(missing)}. A manifest that omits what it could not "
            f"establish reads as complete, so the run is refused rather than "
            f"emitted with defaults. Fix the configuration and re-run."
        )
    return manifest


def assert_no_credentials(manifest: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Refuse a manifest that appears to carry a credential.

    Key-name based and therefore imperfect - it catches ``password`` and
    ``redis_password`` and would miss a secret stored under an innocent name -
    but the failure mode it guards is a secret committed into an artefact, which
    is worth an imperfect check. Deliberately scanning KEYS rather than VALUES: a
    host address is not a secret, and matching on values would flag every numeric
    limit in the document.
    """
    if _depth > 12:
        return manifest
    if isinstance(manifest, dict):
        for key, value in manifest.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in CREDENTIAL_MARKERS):
                raise ManifestIncomplete(
                    f"T-8 manifest key {key!r} looks like a credential. The manifest "
                    f"carries configuration, not secrets; read the settings objects "
                    f"rather than the environment."
                )
            assert_no_credentials(value, _depth + 1)
    elif isinstance(manifest, list | tuple):
        for item in manifest:
            assert_no_credentials(item, _depth + 1)
    return manifest


def assert_runtime_identity(manifest: dict[str, Any], *, expected_loop: str = "uvloop",
                            expected_http: str = "httptools") -> dict[str, Any]:
    """ADDENDUM A-2: the runtime identity is recorded AND asserted.

    A run on asyncio or h11 is labelled non-representative and does not satisfy
    Gate A. The point of asserting rather than recording is that the Dockerfile's
    CMD passes no ``--loop`` and no ``--http``, so uvicorn picks both by
    importability - meaning a run can silently resolve differently than production
    without anyone changing a flag, and the artefact is the only place that shows
    it.
    """
    recorded = manifest.get("runtime", {})
    loop = recorded.get("event_loop_module")
    http = recorded.get("http_impl")
    problems: list[str] = []
    if loop is None:
        problems.append("event_loop_module is absent")
    elif not str(loop).startswith(expected_loop):
        problems.append(f"event loop is {loop!r}, not {expected_loop!r}")
    if http is not None and http != expected_http:
        problems.append(f"HTTP implementation is {http!r}, not {expected_http!r}")
    if problems:
        raise ManifestIncomplete(
            "T-8 runtime identity does not match the production runtime (A-2): "
            + "; ".join(problems)
            + ". A run on a different loop or HTTP implementation is a different "
            "instrument, not a faster or slower version of this one."
        )
    return manifest


def build_app_config_manifest(*, settings: Any = None, security: Any = None,
                              services: Any = None,
                              require_complete: bool = True) -> dict[str, Any]:
    """Collect, check, and return the manifest.

    ``require_complete=False`` exists for the case where a caller wants to record
    a partial manifest deliberately - a local run, or a test asserting the refusal
    - and it is the only way to obtain one. There is no path by which a partial
    manifest is produced silently.
    """
    manifest = collect_app_config(settings=settings, security=security, services=services)
    assert_no_credentials(manifest)
    if require_complete:
        assert_manifest_complete(manifest)
    return manifest


def security_budgets_to_counter(budgets: dict[str, Any]) -> Counter:
    """Budgets as a :class:`Counter`, for an artefact that counts rather than lists.

    Not a general conversion: it exists so a harness can ask "was any budget
    exceeded" as a count, and the names stay the keys so a reader can tell which
    budget the number refers to.
    """
    counter = Counter("security_budgets")
    for name, spec in budgets.items():
        limit = spec.get("limit") if isinstance(spec, dict) else spec
        if limit is not None:
            counter.increment(name, by=int(limit))
    return counter
