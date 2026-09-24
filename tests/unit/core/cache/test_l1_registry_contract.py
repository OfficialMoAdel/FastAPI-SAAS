"""L1 invalidation-channel registry contract (I11).

Every cross-worker L1 cache registers its :class:`InvalidationChannel` at
import time, and the app lifespan starts/stops them all through
``import_l1_owner_modules`` + ``start_all_l1_listeners``.

The failure mode this guards: a new L1 cache adds its channel but nobody
adds its owner module to ``import_l1_owner_modules``. Registration never
runs, the listener never starts, and invalidation publishes into the void —
every OTHER process serves stale suspension/revocation/policy state until
TTL expiry. Silent, and invisible in a unit test that only exercises one
process.

This test asserts the contract at the composition boundary: after importing
the owner modules, the expected channels are present.
"""

from __future__ import annotations

import pytest

from src.app.core.cache import l1
from src.app.core.cache.l1 import (
    import_l1_owner_modules,
    registered_l1_channels,
    start_all_l1_listeners,
)

# The channels the lifespan must start. A new L1 owner module that forgets
# to register (or to be imported by import_l1_owner_modules) drops out of
# this set and this test fails.
_EXPECTED_CHANNELS = frozenset(
    {
        "auth_info_l1",
        "ban_l1",
        "bitmask_l1",
        "rate_limit_policy_l1",
        "tenant_l1",
    }
)


@pytest.fixture
def _import_owner_modules():
    """Import every L1 owner so its channel self-registers."""
    import_l1_owner_modules()
    yield


class TestL1RegistryContract:
    """Every L1 owner module is registered and startable."""

    def test_all_expected_channels_registered(self, _import_owner_modules) -> None:
        """A missing channel means a cross-worker invalidation is void."""
        registered = set(registered_l1_channels())
        missing = _EXPECTED_CHANNELS - registered
        assert not missing, (
            "L1 channels missing from the registry — their owner modules are "
            f"not imported by import_l1_owner_modules: {sorted(missing)}"
        )

    def test_no_unexpected_channels(self, _import_owner_modules) -> None:
        """A new channel must be added to the expected set deliberately.

        An unlisted channel still starts (start_all_l1_listeners iterates the
        whole registry), but the expected set is the documented contract —
        an addition here is a review checkpoint, not a silent side effect.
        """
        registered = set(registered_l1_channels())
        unexpected = registered - _EXPECTED_CHANNELS
        assert not unexpected, (
            "Unknown L1 channels registered — add them to _EXPECTED_CHANNELS "
            f"if they are intentional: {sorted(unexpected)}"
        )

    def test_registration_is_idempotent(self, _import_owner_modules) -> None:
        """Re-importing an owner module must not duplicate or drop channels."""
        before = set(registered_l1_channels())
        import_l1_owner_modules()
        assert set(registered_l1_channels()) == before

    def test_start_starts_every_registered_channel(self, _import_owner_modules) -> None:
        """The lifespan's single start call covers all channels.

        A channel registered but not started is the T9/R1-OPT bug class:
        publishes into the void, other workers serve stale state.
        """
        started = start_all_l1_listeners()
        try:
            assert started == len(registered_l1_channels())
            assert started >= len(_EXPECTED_CHANNELS)
        finally:
            # Channels own their own listener handles; stop via the registry.
            import asyncio

            asyncio.run(l1.stop_all_l1_listeners())
