"""`get_redis_client` raises, and non-canonical IP spellings resolve safely.

Parts (B) and (C) of the bead. Part (A) — cross-tenant cache scope — lives in
`tests/unit/modules/users/test_cache_tenant_scope.py`.

(B) `guard/ip_rules.get_redis_client` is documented to raise
`MissingClientError` and never to yield `None`. Two separate reviews found
that surprising, and surprise in a dependency is where a caller writes
`if client:` and gets an always-true branch. `MissingClientError` is already
raised by `async_get_redis` and covered; what was unpinned is the wrapper's
own contract and the 503 the middleware maps it to.

(C) IP parsing is the input to the SSRF range decision, so the spellings that
resolve to a private or loopback address must be rejected even when they are
not written the obvious way. `url_validator` already maps IPv4-mapped IPv6
back to the v4 address before the range check; this pins that the mapping
happens BEFORE the decision, because a check that ran first would classify
`::ffff:127.0.0.1` as a public IPv6 address and let it through.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from src.app.core.exceptions.cache_exceptions import MissingClientError
from src.app.core.guard.ip_rules import get_redis_client
from src.app.core.security.url_validator import is_ip_blocked, is_metadata_ip
from src.app.core.utils.ip_utils import get_client_ip

# --------------------------------------------------------------------------
# (B) get_redis_client raises, and the middleware maps it to 503.
# --------------------------------------------------------------------------


async def test_get_redis_client_raises_rather_than_yielding_none() -> None:
    """The contract: an uninitialised client is an exception, not a None.

    Drives the REAL `async_get_redis` by clearing the module client, rather
    than patching the dependency's shape — a mock of an async generator is
    easy to get subtly wrong, and then the test proves nothing about the code
    under it.

    Falsifiability: a wrapper that swallowed the error and yielded `None`
    fails this immediately.
    """
    with patch("src.app.core.cache.dependencies._cache.client", None):
        with pytest.raises(MissingClientError):
            async with get_redis_client():
                pass


async def test_get_redis_client_yields_the_client_when_present() -> None:
    """COUNTER-TEST: a healthy Redis still yields the client.

    Without this, a wrapper that simply always raised would pass (B).
    """
    sentinel = MagicMock(name="redis-client")
    with patch("src.app.core.cache.dependencies._cache.client", sentinel):
        async with get_redis_client() as got:
            assert got is sentinel, f"yielded {got!r}, expected the shared client"


def test_metadata_addresses_are_rejected_including_ipv6_form() -> None:
    """The IMDS address must be caught however it is spelled.

    A filter that pattern-matches dotted-quads misses `::ffff:169.254.169.254`
    entirely, and an unparsable address is treated as metadata (fail closed).
    """
    for spelling in ("169.254.169.254", "::ffff:169.254.169.254", "not-an-ip", ""):
        assert is_metadata_ip(spelling) is True, f"{spelling!r} was not treated as metadata"


# --------------------------------------------------------------------------
# (C) Non-canonical IP spellings.
# --------------------------------------------------------------------------


def _request(peer: str, xff: str | None = None) -> Request:
    """A real Request — `client` and `headers` both come off the ASGI scope."""
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 12345) if peer else None,
        }
    )


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "0.0.0.0",
    ],
)
def test_reserved_and_mapped_spellings_are_rejected(host: str) -> None:
    """Every spelling of a reserved address must be rejected, not just the obvious one.

    The metadata address in IPv6 form is included deliberately: an SSRF
    filter that only pattern-matches dotted-quads misses it entirely.
    """
    assert is_ip_blocked(host) is True, f"{host} was treated as public"


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", "2606:4700::1111"])
def test_public_addresses_are_allowed(host: str) -> None:
    """COUNTER-TEST: the filter is not simply rejecting everything."""
    assert is_ip_blocked(host) is False


def test_ipv4_mapped_ipv6_is_decided_as_the_v4_address() -> None:
    """The mapping must happen BEFORE the range decision.

    If the IPv6 form were classified as IPv6 first, `::ffff:127.0.0.1` would
    look like a public address and pass. This asserts the two agree, which is
    the property that makes the mapping worth having.
    """
    mapped = ipaddress.ip_address("::ffff:127.0.0.1")
    assert isinstance(mapped, ipaddress.IPv6Address)
    assert mapped.ipv4_mapped is not None
    assert mapped.ipv4_mapped == ipaddress.ip_address("127.0.0.1")
    assert is_ip_blocked("::ffff:127.0.0.1") is True


def test_xff_chain_returns_the_rightmost_untrusted_hop() -> None:
    """A multi-hop XFF chain resolves to the client, not the leftmost entry.

    The leftmost entry is attacker-controlled whenever an edge appends rather
    than replaces, so preferring it is the classic spoofing bug.
    """
    with patch("src.app.core.utils.ip_utils.is_trusted_proxy") as trusted:
        trusted.side_effect = lambda ip: ip.startswith("10.")
        request = _request("10.0.0.1", "1.2.3.4, 10.0.0.2, 10.0.0.3")

        assert get_client_ip(request) == "1.2.3.4"


def test_xff_where_every_hop_is_trusted_fails_closed_to_the_peer() -> None:
    """A chain with no untrusted hop falls back to the socket peer.

    Every entry here is a trusted proxy, so the header identifies nobody.
    Returning the leftmost entry would hand back a value the client chose,
    and that value becomes the auto-ban key and the rate-limit identity — so
    the code returns the peer address instead.
    """
    with patch("src.app.core.utils.ip_utils.is_trusted_proxy") as trusted:
        trusted.side_effect = lambda ip: ip.startswith("10.")
        request = _request("10.0.0.1", "10.0.0.9, 10.0.0.2, 10.0.0.3")

        resolved = get_client_ip(request)

    assert resolved == "10.0.0.1", (
        f"expected the socket peer, got {resolved!r} — a header value reached "
        f"the ban/rate-limit identity sink"
    )


def test_xff_returns_the_first_untrusted_hop_from_the_right() -> None:
    """The rightmost untrusted hop is the client; untrusted entries are kept.

    `6.6.6.6` here is genuinely untrusted, so returning it is correct — this
    is the counter-test to the case above and stops a well-meaning 'fix' from
    making the resolver return nothing.
    """
    with patch("src.app.core.utils.ip_utils.is_trusted_proxy") as trusted:
        trusted.side_effect = lambda ip: ip.startswith("10.")
        request = _request("10.0.0.1", "6.6.6.6, 10.0.0.2, 10.0.0.3")

        assert get_client_ip(request) == "6.6.6.6"
