"""X-Forwarded-For handling must never return a client-controlled value.

``get_client_ip`` is the source of the ban key (ip_rules.ban_ip), the
rate-limit identity, and the audit trail. When every hop in the
X-Forwarded-For chain is a trusted proxy there is no untrusted hop to
identify the client, and the previous code returned ``ips[0]`` — the
LEFTMOST entry, which is exactly the one an attacker writes when the edge
appends rather than overwrites. That is a ban-evasion primitive on the
exact string used to key a ban.
"""

from unittest.mock import patch

import pytest

from src.app.core.utils.ip_utils import get_client_ip


def _request(peer: str | None, xff: str | None, x_real_ip: str | None = None):
    class _Client:
        def __init__(self, host):
            self.host = host

    class _Request:
        def __init__(self):
            self.client = _Client(peer) if peer else None
            self.headers: dict[str, str] = {}
            if xff is not None:
                self.headers["X-Forwarded-For"] = xff
            if x_real_ip is not None:
                self.headers["X-Real-IP"] = x_real_ip

    return _Request()


TRUSTED_PEER = "10.0.0.1"   # the direct peer, a trusted proxy
TRUSTED_EDGE = "10.0.0.2"   # an appended trusted hop
UNTRUSTED = "203.0.113.9"   # a public address


class TestAllTrustedXffChain:
    """Every hop trusted => the chain cannot identify a client.

    The attack this closes: an edge that APPENDS lets the attacker seed
    the left of the chain. If the attacker seeds an address inside a
    trusted range and the edge appends its own trusted hop, every entry
    is trusted — and pre-fix the code handed back ips[0], i.e. an
    attacker-chosen address. Because that value becomes the ban key, the
    attacker can pick a DIFFERENT one per request and walk out of a ban.
    """

    def test_all_trusted_chain_does_not_return_the_leftmost_entry(self) -> None:
        """Pre-fix this returned the attacker-seeded ips[0]."""
        seeded = "10.0.0.5"  # attacker-chosen, inside a trusted range
        req = _request(peer=TRUSTED_PEER, xff=f"{seeded}, {TRUSTED_EDGE}")

        with patch("src.app.core.utils.ip_utils.is_trusted_proxy", return_value=True):
            resolved = get_client_ip(req)

        assert resolved != seeded, (
            f"returned the leftmost XFF entry ({resolved}); that value is "
            "attacker-seeded and becomes the ban key"
        )
        assert resolved == TRUSTED_PEER, "must fail closed to the peer address"

    def test_single_trusted_entry_does_not_return_the_header_value(self) -> None:
        """Header value and peer differ here, so the header must be ignored."""
        seeded = "10.0.0.7"
        req = _request(peer=TRUSTED_PEER, xff=seeded)

        with patch("src.app.core.utils.ip_utils.is_trusted_proxy", return_value=True):
            resolved = get_client_ip(req)

        assert resolved == TRUSTED_PEER

    def test_spoofed_trusted_range_entry_cannot_dodge_an_ip_keyed_ban(self) -> None:
        """The end-to-end property: the ban key is not attacker-selectable.

        Three requests, three attacker-chosen seeds, one banned peer — all
        must resolve to the same key, so a ban cannot be sidestepped by
        varying the header.
        """
        resolutions = set()
        for seeded in ("10.0.0.5", "10.0.0.6", "10.0.0.7"):
            req = _request(peer=TRUSTED_PEER, xff=f"{seeded}, {TRUSTED_EDGE}")
            with patch("src.app.core.utils.ip_utils.is_trusted_proxy", return_value=True):
                resolutions.add(get_client_ip(req))

        assert resolutions == {TRUSTED_PEER}, (
            f"attacker varied the seed to change the ban key: {resolutions}"
        )


class TestNormalXffChainsStillWork:
    def test_rightmost_untrusted_hop_wins(self) -> None:
        req = _request(peer=TRUSTED_PEER, xff=f"{UNTRUSTED}, {TRUSTED_EDGE}")
        # The peer must itself be trusted or get_client_ip ignores XFF entirely.
        trusted = {TRUSTED_PEER, TRUSTED_EDGE}

        with patch(
            "src.app.core.utils.ip_utils.is_trusted_proxy",
            side_effect=lambda ip: ip in trusted,
        ):
            assert get_client_ip(req) == UNTRUSTED

    def test_leftmost_untrusted_is_found_even_when_not_first_scanned(self) -> None:
        other = "198.51.100.5"
        req = _request(peer=TRUSTED_PEER, xff=f"{other}, {UNTRUSTED}, {TRUSTED_EDGE}")
        trusted = {TRUSTED_PEER, TRUSTED_EDGE}

        with patch(
            "src.app.core.utils.ip_utils.is_trusted_proxy",
            side_effect=lambda ip: ip in trusted,
        ):
            assert get_client_ip(req) == UNTRUSTED


class TestUntrustedPeerIsUnaffected:
    @pytest.mark.parametrize("xff", ["203.0.113.1, 10.0.0.1", "1.2.3.4"])
    def test_header_ignored_when_peer_is_not_trusted(self, xff: str) -> None:
        peer = "198.51.100.1"
        req = _request(peer=peer, xff=xff)

        with patch("src.app.core.utils.ip_utils.is_trusted_proxy", return_value=False):
            assert get_client_ip(req) == peer
