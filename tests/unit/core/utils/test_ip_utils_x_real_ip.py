"""P1-3: X-Real-IP must not resolve client identity — the peer address does.

``get_client_ip`` feeds five security sinks: the login budget key, the refresh
budget key, the auto-ban key, the audit-trail IP, and the ip_filter
blacklist/whitelist decision itself. The last one makes a spoofed X-Real-IP a
ban-evasion and lockout-evasion primitive whenever the edge forwards rather
than overwrites it.

The XFF path (right-to-left, skipping trusted hops, failing closed to the
peer) remains the only header-derived identity. These tests pin the removal
of the X-Real-IP fallback, the trusted-proxy guard that preceded it, and the
diagnostic warning a now-misconfigured edge will see.

``is_trusted_proxy`` is patched with a side_effect throughout: under test
settings the fixture peers are NOT trusted by default, and an unpatched test
would skip the branch it claims to cover.

CAPTURE STRATEGY — why not ``structlog.testing.capture_logs()``:
that helper swaps the GLOBAL processor chain, and a module-level structlog
proxy caches itself against whatever chain was current the first time it
bound (``structlog``'s ``BoundLoggerLazyProxy.bind`` replaces the instance's
own ``bind`` with a closure over that chain). Any earlier test in the run that
boots the app — ``composition.lifespan_with_admin`` calls ``setup_logging()``,
which sets ``cache_logger_on_first_use=True`` — therefore makes the capture
context a silent no-op for ``ip_utils.logger``, and the event lands on the real
handlers instead. That is exactly how these two tests came to pass in isolation
and fail in a full run.

So the diagnostic is captured by substituting the module's own logger object
for the duration of the call, the same pattern
``tests/unit/core/security/test_pii_and_fail_open_knobs.py`` uses. It
intercepts the kwargs at the call site and cannot be perturbed by whatever
another test left configured globally.
"""

from typing import Any
from unittest.mock import patch

import pytest

from src.app.core.utils.ip_utils import get_client_ip


class _RecordingLogger:
    """A logger-shaped object recording the event dicts it is called with."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _record(self, event: Any, kwargs: dict[str, Any]) -> None:
        record = dict(kwargs)
        record["event"] = event
        self.events.append(record)

    def debug(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def info(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def warning(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def error(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def critical(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

    def exception(self, event: Any, **kwargs: Any) -> None:
        self._record(event, kwargs)

TRUSTED_PEER = "10.0.0.1"  # a trusted proxy, observed at the socket
TRUSTED_EDGE = "10.0.0.2"  # a trusted hop inside a chain
TRUSTED_PROXY2 = "10.0.0.3"  # a second trusted intermediary
UNTRUSTED = "203.0.113.9"  # a public address

_TRUSTED = {TRUSTED_PEER, TRUSTED_EDGE, TRUSTED_PROXY2}


def _request(peer: str, xff: str | None = None, x_real_ip: str | None = None):
    class _Client:
        def __init__(self, host):
            self.host = host

    class _Request:
        def __init__(self):
            self.client = _Client(peer)
            self.headers: dict[str, str] = {}
            if xff is not None:
                self.headers["X-Forwarded-For"] = xff
            if x_real_ip is not None:
                self.headers["X-Real-IP"] = x_real_ip

    return _Request()


def _trusted_peer_calls():
    """The peer and edge are trusted; everything else is not."""
    return patch(
        "src.app.core.utils.ip_utils.is_trusted_proxy",
        side_effect=lambda ip: ip in _TRUSTED,
    )


class TestXRealIpIsIgnored:
    """No path reads X-Real-IP for identity any more."""

    @pytest.mark.parametrize("spoofed", ["203.0.113.50", "9.9.9.9", UNTRUSTED])
    def test_spoofed_x_real_ip_yields_the_peer(self, spoofed: str) -> None:
        """A forwarded client value must not reach the ban key / budget keys.

        The peer IS trusted here — that is the condition under which the old
        fallback accepted the header.
        """
        req = _request(peer=TRUSTED_PEER, x_real_ip=spoofed)
        with _trusted_peer_calls():
            assert get_client_ip(req) == TRUSTED_PEER

    def test_trusted_proxy_valued_x_real_ip_yields_the_peer(self) -> None:
        """Pin the guard the upstream reports missed — it must never regress."""
        req = _request(peer=TRUSTED_PEER, x_real_ip=TRUSTED_EDGE)
        with _trusted_peer_calls():
            assert get_client_ip(req) == TRUSTED_PEER

    @pytest.mark.parametrize("garbage", ["unknown", "1.2.3.4, 5.6.7.8", "not-an-ip", "", "  "])
    def test_unparseable_x_real_ip_yields_the_peer(self, garbage: str) -> None:
        """Lists, "unknown" and junk are ignored — never parsed, never trusted."""
        req = _request(peer=TRUSTED_PEER, x_real_ip=garbage)
        with _trusted_peer_calls():
            assert get_client_ip(req) == TRUSTED_PEER

    def test_direct_untrusted_peer_ignores_the_header(self) -> None:
        """Without a trusted peer the header was never consulted — unchanged."""
        req = _request(peer=UNTRUSTED, x_real_ip="1.2.3.4")
        with _trusted_peer_calls():
            assert get_client_ip(req) == UNTRUSTED


class TestXffPathUnregressed:
    """COUNTER-TEST: the header path that was already right stays right."""

    def test_rightmost_untrusted_hop_wins(self) -> None:
        req = _request(peer=TRUSTED_PEER, xff=f"{UNTRUSTED}, {TRUSTED_EDGE}")
        with _trusted_peer_calls():
            assert get_client_ip(req) == UNTRUSTED

    def test_multi_hop_chain_resolves_the_real_client(self) -> None:
        """Realistic chain: client at the left, every intermediary trusted."""
        req = _request(peer=TRUSTED_PEER, xff=f"{UNTRUSTED}, {TRUSTED_PROXY2}, {TRUSTED_EDGE}")
        with _trusted_peer_calls():
            assert get_client_ip(req) == UNTRUSTED

    def test_xff_wins_over_x_real_ip(self) -> None:
        """Both headers present: XFF is the identity, X-Real-IP never read."""
        req = _request(peer=TRUSTED_PEER, xff=UNTRUSTED, x_real_ip="172.16.0.50")
        with _trusted_peer_calls():
            assert get_client_ip(req) == UNTRUSTED

    def test_all_trusted_chain_fails_closed_to_peer(self) -> None:
        seeded = "10.0.0.5"
        req = _request(peer=TRUSTED_PEER, xff=f"{seeded}, {TRUSTED_EDGE}")
        with patch("src.app.core.utils.ip_utils.is_trusted_proxy", return_value=True):
            assert get_client_ip(req) == TRUSTED_PEER


class TestDiagnosableMisconfiguration:
    """A deployed edge sending only X-Real-IP must be VISIBLE, not silent.

    Operators upgrading from the fallback will stop resolving real client
    IPs until their edge also sends X-Forwarded-For. The warning names the
    header, the peer, and the remediation.

    Captured with structlog.testing.capture_logs rather than caplog: this
    project's structlog pipeline renders structurally and never reaches the
    stdlib logging records caplog listens on.
    """

    @staticmethod
    def _capture(req) -> list[dict]:
        """Record the diagnostic by substituting the module's own logger."""
        recorder = _RecordingLogger()
        with patch("src.app.core.utils.ip_utils.logger", recorder):
            with _trusted_peer_calls():
                get_client_ip(req)
        return recorder.events

    def test_ignored_header_is_logged(self) -> None:
        records = self._capture(_request(peer=TRUSTED_PEER, x_real_ip="203.0.113.55"))
        events = [r["event"] for r in records]
        assert "client_ip_x_real_ip_ignored" in events, f"no diagnostic emitted: {events}"

    def test_warning_carries_the_remediation(self) -> None:
        records = self._capture(_request(peer=TRUSTED_PEER, x_real_ip="203.0.113.55"))
        record = next(r for r in records if r["event"] == "client_ip_x_real_ip_ignored")
        assert record["peer"] == TRUSTED_PEER
        assert "X-Forwarded-For" in str(record.get("remediation", "")), (
            f"remediation missing from diagnostic: {record}"
        )

    def test_no_warning_on_the_normal_xff_path(self) -> None:
        """No noise on the path every correctly configured edge takes."""
        records = self._capture(
            _request(peer=TRUSTED_PEER, xff=UNTRUSTED, x_real_ip="172.16.0.50")
        )
        assert not [r for r in records if "x_real_ip" in str(r["event"])]

    def test_no_warning_for_direct_clients(self) -> None:
        """A plain request with no proxy headers never warns."""
        records = self._capture(_request(peer=UNTRUSTED))
        assert not [r for r in records if "x_real_ip" in str(r["event"])]
