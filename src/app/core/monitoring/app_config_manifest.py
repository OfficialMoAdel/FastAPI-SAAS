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

import json
import os
import platform
import sys
from pathlib import Path
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
        except (AttributeError, TypeError, KeyError, ValueError):
            # The specific ways a settings read fails: no such attribute, the
            # flat accessor is not callable, no such key, or a pydantic
            # ValidationError (which subclasses ValueError). Anything broader
            # would swallow a genuine defect in a caller and read it as "this
            # setting is unset" - which is how an incomplete manifest comes to
            # look complete.
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


def resolve_http_impl() -> str:
    """The HTTP implementation uvicorn uses for ``--http auto``.

    Delegated to :mod:`src.benchmarks.run_manifest` rather than reimplemented,
    because that module already resolves it off uvicorn's OWN bound symbol - and
    a second copy of that decision is a second thing to rot. An unrecognised
    resolution raises there rather than returning a guess, which is the right
    behaviour: an HTTP implementation this function cannot name is not a runtime
    to report a number from.
    """
    from src.benchmarks.run_manifest import resolve_http_impl as _resolve

    try:
        return _resolve()
    except (RuntimeError, ImportError, AttributeError) as error:
        # RuntimeError is what resolve_http_impl raises for an unrecognised
        # resolution; ImportError is a host without uvicorn. Recorded as absent,
        # never guessed.
        return f"unresolved: {error}"


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
        except (ImportError, ValueError):
            # ImportError: the module is absent. ValueError: pydantic rejected
            # the environment (ValidationError subclasses it). Either way the
            # field is recorded as a hole and the run is refused by the
            # completeness check - which is the point.
            security = None
    if services is None:
        try:
            from src.app.core.config.services import DefaultRateLimitSettings

            services = DefaultRateLimitSettings()
        except (ImportError, ValueError):
            services = None

    postgres = getattr(settings, "postgres", None)
    redis_cache = getattr(settings, "redis_cache", None)
    redis_queue = getattr(settings, "redis_queue", None)
    if redis_cache is None or redis_queue is None:
        try:
            from src.app.core.config.services import RedisQueueSettings

            redis_queue = redis_queue if redis_queue is not None else RedisQueueSettings()
        except (ImportError, ValueError):
            redis_queue = None

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
            "event_loop_resolved": _resolved_loop(),
            "http_impl": resolve_http_impl(),
            "image_digest": _image_digest(),
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
    except RuntimeError:
        # get_event_loop raises RuntimeError when the thread has no loop, which
        # is entirely normal while a manifest is being built. Recorded as absent;
        # the A-2 assertion treats an absent runtime identity as a failure.
        return None


def _image_digest() -> str | None:
    """The OCI image digest this process was started from.

    Read from ``PERF_IMAGE_DIGEST``, declared by the deployment. No process can
    ask its own orchestrator which image it is running without the orchestrator's
    API, so this is DECLARED and validated - never synthesised, and absent rather
    than guessed. A container runtime knows the digest it pulled; nothing inside
    the process can derive it, so a manifest that reported a fabricated digest
    would be worse than one that reports none.
    """
    from src.benchmarks.run_manifest import IMAGE_DIGEST_ENV

    return (os.environ.get(IMAGE_DIGEST_ENV) or "").strip() or None


def _resolved_loop() -> str | None:
    """The loop uvicorn would install for ``--loop auto`` on THIS host.

    Distinct from ``event_loop_module`` (what this thread currently has running)
    because A-2 is about what the Dockerfile's flagless CMD SELECTS, not about
    whatever a manifest-building thread happens to be doing. They are usually the
    same; they are not the same question, so both are recorded.

    Delegated to :func:`src.benchmarks.run_manifest.resolve_event_loop`, which
    already reads uvicorn's own resolution. An earlier version here tried to walk
    ``uvicorn.config.LOOP_SETUPS`` itself and returned None, because the entry is a
    function whose ``__name__`` is ``"auto_loop_setup"`` - a constant that says
    nothing about WHICH loop it installs. That failure mode is worth naming: the
    field read as "not recorded" rather than as "the resolver was wrong", and an
    absent runtime identity is exactly the omission A-2 exists to close.
    """
    from src.benchmarks.run_manifest import resolve_event_loop

    try:
        return resolve_event_loop()
    except (ImportError, ValueError, RuntimeError):
        # find_spec raises ImportError/ValueError, including the ValueError a
        # module already in sys.modules with __spec__ = None raises. Recorded as
        # absent, never guessed.
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


def assert_runtime_identity(
    manifest: dict[str, Any],
    *,
    expected_loop: str = "uvloop",
    expected_http: str = "httptools",
) -> dict[str, Any]:
    """ADDENDUM A-2: the runtime identity is recorded AND asserted, and both halves must be present.

    Acceptance: "the manifest contains all five values and the assertion fails the
    run when the loop or HTTP class does not match the Dockerfile CMD; a mismatched
    run is labelled non-representative in the artefact rather than silently
    accepted."

    Two properties this had to earn, both by failing first:

    **Absence is a failure, not a pass.** An earlier version only compared the HTTP
    implementation ``if http is not None``, so a manifest that never resolved it
    skipped the HTTP check entirely and passed. The loop check handled absence;
    the HTTP one did not, and the asymmetry meant the most-missed field was the one
    that could be missed silently. A field that is absent is now a failure for
    both, because "we did not record it" and "it matched" are not the same fact.

    **All five values are required**, not just the two that gate. Python version,
    resolved loop and HTTP implementation are what let a reader place a number;
    image digest is what makes it about a specific build. Two of them failing the
    assertion is enough to refuse, but a manifest missing any of them is not a
    manifest that can be asserted on.
    """
    runtime = manifest.get("runtime", {})
    missing = [
        field
        for field in ("python_version", "event_loop_resolved", "event_loop_module", "http_impl", "image_digest")
        if not runtime.get(field)
    ]
    problems: list[str] = []
    if missing:
        problems.append(f"not recorded: {missing} (A-2 requires all five)")

    resolved = runtime.get("event_loop_resolved")
    if resolved and resolved != expected_loop:
        problems.append(f"--loop auto resolves to {resolved!r}, not {expected_loop!r}")
    http = runtime.get("http_impl")
    if http and http != expected_http:
        problems.append(f"--http auto resolves to {http!r}, not {expected_http!r}")

    if problems:
        raise ManifestIncomplete(
            "T-8/A-2 runtime identity does not match the production runtime: "
            + "; ".join(problems)
            + ". Dockerfile's CMD passes no --loop and no --http, so uvicorn selects "
            "both by importability and a run can resolve differently than production "
            "without anyone changing a flag. This run is NON-REPRESENTATIVE: a "
            "different instrument, not a faster or slower version of this one."
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


def write_app_config_manifest(output: Path, *, settings: Any = None, security: Any = None,
                             services: Any = None, require_complete: bool = True) -> Path:
    """Build, check and WRITE the manifest beside the artefact.

    The write is separate from :func:`build_app_config_manifest` so the check and
    the emission cannot be separated by a caller who forgets one. A manifest built
    and never written leaves the run with no record of what it ran with, which is
    the omission unified plan §6.2 calls "the single omission that let every prior
    run be uninterpretable" - so the artefact and its manifest are produced by one
    call or not at all.

    Written with ``sort_keys`` so two runs of the same configuration diff cleanly,
    which is what makes a manifest useful for spotting what CHANGED between runs
    rather than only what it said.

    ``require_complete`` exists for a caller that deliberately wants a partial
    manifest - a local run, or a test asserting the refusal. There is no path by
    which a partial manifest reaches an artefact silently.
    """
    manifest = build_app_config_manifest(
        settings=settings, security=security, services=services, require_complete=require_complete
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


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
