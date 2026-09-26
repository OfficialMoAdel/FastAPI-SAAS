"""The L1 cache seam: one key grammar and one pub/sub discipline per cache.

Five caches in this codebase are process-local ``TTLCache``s that must stay in
step across workers: tenant resolution, the ban negative cache, tier rate-limit
policies, the RBAC bitmask, and user auth-info. Each hand-rolled the same eight
pieces — the ``TTLCache``, an ``on_message`` body, an ``InvalidationChannel``, a
late ``register_l1_channel`` import, start/stop aliases, and a publish helper —
and each invented its own payload grammar for the channel.

This module owns the three things that were being re-derived by hand:

* **the broadcast token** — ``"*"`` (and, for two owners, ``""``) means "drop
  everything". The five copies spelled this five different ways.
* **the drop-local-then-publish ordering** — Redis pub/sub does not echo to the
  publisher, so an owner that publishes before dropping keeps serving the entry
  it just invalidated. Every owner wrote this out longhand, with a comment.
* **the companion-lockstep rule** — a cache *derived* from another cache (a
  parsed object, a compiled index) must never outlive it. Passing every layer in
  ``caches`` makes that structural rather than a comment.

The seam deliberately **wraps** the existing ``TTLCache`` objects instead of
replacing them: every ``get``/``put`` on these caches sits on a per-request path,
and it is the invalidation half that was duplicated. Reads stay direct, so this
module adds no hot-path indirection and changes no read behaviour.

Per ADR-0004 the L1s and their pub/sub channels are kept by decision — this
consolidates the *glue*, not the policy. TTLs, ``maxsize`` values, and the
eventual-consistency trade-off are untouched.

The module also owns the **registry**. Every seam self-registers as it is
constructed, so the app lifespan starts and stops all listeners through a single
call instead of importing five owner modules and hand-calling five start/stop
pairs. The failure mode the registry closes is unchanged from I11: an L1 that
wires its channel but is never started publishes into the void, and every other
process serves stale suspension/revocation/policy state until TTL expiry.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from cachetools import TTLCache

from src.app.core.cache.invalidation_channel import InvalidationChannel
from src.app.core.logger import get_logger

logger = get_logger(__name__)

#: The wire token meaning "drop every entry".
BROADCAST_TOKEN: Final = "*"


class _Sentinel:
    """A distinct, non-string marker, so it can never collide with a real key."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return f"<{self._name}>"


#: ``KeyCodec.parse`` result meaning "this payload applies to everything".
BROADCAST: Final = _Sentinel("BROADCAST")
#: ``KeyCodec.parse`` result meaning "this payload is not ours; leave the cache alone".
IGNORE: Final = _Sentinel("IGNORE")


class KeyCodec(Protocol):
    """How one L1 maps between its cache keys and its channel payloads.

    ``empty_is_broadcast`` is the one grammar detail that is genuinely not
    shared: an empty payload means "drop everything" for the bitmask and
    auth-info caches, but for tenant/ban/rate-limit it is just another payload
    that parses to a harmless key or to ``IGNORE``. It is declared explicitly
    rather than inferred, because getting it wrong silently widens or narrows
    what a stray empty message flushes.

    Declared as a read-only property so the frozen dataclass codecs below
    satisfy it.
    """

    @property
    def empty_is_broadcast(self) -> bool: ...

    def encode(self, key: Hashable) -> str:
        """Cache key -> wire payload."""
        ...

    def parse(self, payload: str) -> Hashable | _Sentinel:
        """Wire payload -> cache key, ``IGNORE``, or ``BROADCAST``.

        The broadcast token has already been handled by :meth:`L1Cache.handle`,
        so an implementation only has to cope with a genuine key payload.
        """
        ...


@dataclass(frozen=True)
class RawKeyCodec:
    """The wire payload *is* the cache key — tenant slugs, auth-info keys."""

    empty_is_broadcast: bool = False

    def encode(self, key: Hashable) -> str:
        return str(key)

    def parse(self, payload: str) -> Hashable:
        return payload


@dataclass(frozen=True)
class PrefixedKeyCodec:
    """The wire payload omits a prefix the cache key carries.

    The ban cache is keyed ``ban:{ip}`` but its channel carries the bare IP, so
    the transform has to live somewhere; it lives here, in one place, instead of
    being re-derived at each publish site.
    """

    prefix: str
    empty_is_broadcast: bool = False

    def encode(self, key: Hashable) -> str:
        text = str(key)
        return text[len(self.prefix) :] if text.startswith(self.prefix) else text

    def parse(self, payload: str) -> Hashable:
        return f"{self.prefix}{payload}"


@dataclass(frozen=True)
class IntKeyCodec:
    """Numeric keys, with a **fail-open** guard on malformed payloads.

    A malformed tier id must not flush the fleet's policy cache, so it is
    ignored rather than broadcast. This is a deliberate choice preserved from
    the hand-rolled handler — see :class:`PairKeyCodec` for the opposite policy.
    """

    empty_is_broadcast: bool = False

    def encode(self, key: Hashable) -> str:
        return str(cast("int", key))

    def parse(self, payload: str) -> Hashable | _Sentinel:
        return int(payload) if payload.isdigit() else IGNORE


@dataclass(frozen=True)
class PairKeyCodec:
    """``"{first}:{second}"`` keys, with a **fail-safe** guard on malformed payloads.

    The opposite policy to :class:`IntKeyCodec`, and equally deliberate: an
    unparseable bitmask payload must not leave a revoked permission cached, so
    it clears everything.

    A payload without the separator (e.g. ``"5"``) is malformed and
    broadcasts — ``partition`` never fails, so separator presence is checked
    explicitly rather than relying on ``ValueError`` alone.
    """

    separator: str = ":"
    empty_is_broadcast: bool = False

    def encode(self, key: Hashable) -> str:
        first, second = cast("tuple[object, object]", key)
        return f"{first}{self.separator}{second}"

    def parse(self, payload: str) -> Hashable | _Sentinel:
        if self.separator not in payload:
            return BROADCAST
        first, _, second = payload.partition(self.separator)
        try:
            return (int(first), second)
        except ValueError:
            return BROADCAST


class L1Cache:
    """One key-space: N parallel caches dropped in lockstep, plus its channel.

    ``caches`` is a sequence because two owners keep a companion cache that must
    never outlive its source — the tenant parsed-object memo and the compiled
    rate-limit path map. Passing both makes that invariant structural; the
    original code carried it in a comment and a code review.
    """

    def __init__(
        self,
        *,
        name: str,
        channel: str,
        codec: KeyCodec,
        caches: Sequence[TTLCache[Any, Any]],
        logger_name: str | None = None,
    ) -> None:
        if not caches:
            raise ValueError(f"L1Cache {name!r} requires at least one cache")
        self.name = name
        self._codec = codec
        self._caches = tuple(caches)
        self._channel = InvalidationChannel(
            channel=channel,
            on_message=self.handle,
            logger_name=logger_name or name,
        )
        register_l1_channel(name, self._channel)

    # -- local, safe to call from synchronous code -------------------------

    def drop(self, key: Hashable) -> None:
        """Drop one key from every layer of this key-space."""
        for cache in self._caches:
            cache.pop(key, None)

    def clear(self) -> None:
        """Drop every key from every layer of this key-space."""
        for cache in self._caches:
            cache.clear()

    # -- the channel's inbound grammar -------------------------------------

    def handle(self, payload: str) -> None:
        """Apply one wire payload. Bound as the channel's ``on_message``."""
        if payload == BROADCAST_TOKEN or (self._codec.empty_is_broadcast and not payload):
            self.clear()
            return
        key = self._codec.parse(payload)
        if key is BROADCAST:
            self.clear()
        elif key is not IGNORE:
            self.drop(key)

    # -- cross-worker ------------------------------------------------------

    async def invalidate(self, key: Hashable | None = None) -> None:
        """Drop locally, **then** publish — in that order.

        Pub/sub does not echo to the publisher, so publishing first would leave
        this process serving the very entry it just invalidated. ``None`` means
        "everything", locally and on the wire.

        The local drop goes through the codec round-trip
        (``parse(encode(key))``) so a caller-held key outside the wire domain
        (e.g. a bare IP for the prefixed ban cache) drops the same entry the
        remotes drop — local and remote always agree.
        """
        if key is None:
            self.clear()
        else:
            normalized = self._codec.parse(self._codec.encode(key))
            if normalized is BROADCAST:
                self.clear()
            elif normalized is not IGNORE:
                self.drop(normalized)
        await self.publish(None if key is None else self._codec.encode(key))

    async def publish(self, payload: str | None = None) -> None:
        """Fan a raw wire payload out to the other workers."""
        await self._channel.publish(payload)

    # -- introspection -----------------------------------------------------

    @property
    def channel(self) -> InvalidationChannel:
        """The underlying pub/sub channel.

        Exposed so owner modules can keep their historical re-exports alive
        while importers migrate (``middleware/tenant.py`` re-exports tenancy's
        channel name). New code should call :meth:`invalidate` instead.
        """
        return self._channel

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the background subscriber (idempotent)."""
        self._channel.start()

    async def stop(self) -> None:
        """Stop the background subscriber (idempotent, awaitable)."""
        await self._channel.stop()


# ---------------------------------------------------------------------------
# Registry — one place that knows every L1, so the lifespan has one start call.
# ---------------------------------------------------------------------------

# name -> channel. Registration happens at owner-module import time, so this dict
# is populated before the lifespan phase runs.
_CHANNELS: dict[str, InvalidationChannel] = {}


def register_l1_channel(name: str, channel: InvalidationChannel) -> InvalidationChannel:
    """Register an L1 invalidation channel under a stable name.

    Called by :meth:`L1Cache.__init__`; not intended to be called directly.
    Idempotent per name — re-registration with a different channel object (e.g.
    a module re-import under a test runner) logs loudly and replaces.
    """
    existing = _CHANNELS.get(name)
    if existing is not None and existing is not channel:
        logger.warning("l1_channel_reregistered", name=name)
    _CHANNELS[name] = channel
    return channel


def registered_l1_channels() -> tuple[str, ...]:
    """Names of all registered channels (for tests/observability)."""
    return tuple(_CHANNELS)


def start_all_l1_listeners() -> int:
    """Start every registered L1 listener. Returns the count started.

    Callers must first import every L1-owning module (see
    :func:`import_l1_core_owner_modules`) so registration has happened.
    """
    started = 0
    for name, channel in _CHANNELS.items():
        try:
            channel.start()
            started += 1
        except Exception as e:  # noqa: BLE001 - one bad listener must not block the rest
            logger.error("l1_listener_start_failed", name=name, error=str(e))
    logger.info("l1_listeners_started", count=started, channels=list(_CHANNELS))
    return started


async def stop_all_l1_listeners() -> int:
    """Stop every registered L1 listener. Returns the count stopped."""
    stopped = 0
    for name, channel in _CHANNELS.items():
        try:
            await channel.stop()
            stopped += 1
        except Exception as e:  # noqa: BLE001 - teardown must not mask shutdown
            logger.error("l1_listener_stop_failed", name=name, error=str(e))
    logger.info("l1_listeners_stopped", count=stopped)
    return stopped


def import_l1_core_owner_modules() -> None:
    """Import the *core* L1-owning modules so their seams self-register.

    Core owns the L1 mechanism and the four owners that live beside it. The
    fifth owner is a feature module (users.cache), and naming it from here
    would be a ``core -> feature`` edge, so the full enumeration is composed
    at the application seam: ``composition.import_l1_owner_modules`` calls
    this and adds the feature owner. Splitting it that way keeps one
    enumeration, still explicit, still in a place that may name features.

    Single enumeration point: adding an L1 means (1) declaring its seam in
    the owner module and (2) adding one import to the composition-root
    function. Forgetting (2) is caught by the contract test asserting the
    expected channel set.

    This list is the price of a deterministic boot. Python has no reliable
    auto-discovery for "every module that declares an L1", and a scanning
    approach would reintroduce exactly the silent publish-into-the-void
    failure the registry exists to prevent — so it stays explicit.
    """
    from src.app.core.rbac import dependencies as _rbac_deps  # noqa: F401
    from src.app.core.security import auto_ban as _auto_ban  # noqa: F401
    from src.app.core.security import rate_limit_cache as _rate_limit_cache  # noqa: F401
    from src.app.core.tenancy import cache as _tenancy_cache  # noqa: F401


__all__ = [
    "BROADCAST",
    "BROADCAST_TOKEN",
    "IGNORE",
    "IntKeyCodec",
    "KeyCodec",
    "L1Cache",
    "PairKeyCodec",
    "PrefixedKeyCodec",
    "RawKeyCodec",
    "import_l1_core_owner_modules",
    "register_l1_channel",
    "registered_l1_channels",
    "start_all_l1_listeners",
    "stop_all_l1_listeners",
]
