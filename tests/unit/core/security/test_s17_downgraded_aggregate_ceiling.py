"""S-17 — a downgraded (revoked) token must still face the anonymous ceiling.

`RequestAdmissionService._resolve_legacy_config` detects a revoked token and
downgrades the caller to anonymous: it rebinds a LOCAL `user_id = None` and
re-keys the request into the anonymous per-path bucket under anonymous limits.

But `evaluate()` gated the IP-keyed aggregate ceiling on the CALLER's ctx:

    if not is_limited and not ctx.user_id:   # ctx still held the pre-downgrade sub

So a caller with a revoked token — who had just been reclassified as
anonymous — was exempted from the one ceiling that survives path rotation. The
comment directly above that gate names the attack it exists to close. The
caller owns their own revoked token, so the bypass is attacker-controlled.

The resolver now reports the downgrade explicitly, and `evaluate()` rebinds
`ctx.user_id` before the ceiling gate runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.core.security.request_admission import RequestAdmissionService, _RateLimitContext


def _ctx(*, user_id: str | None, token: str | None) -> _RateLimitContext:
    return _RateLimitContext(
        user_id=user_id,
        tier_name="free",
        tenant_id=None,
        tier_id_raw=1,
        token=token,
        jti=None,
        path="/api/v1/x",
    )


def _request(path: str = "/api/v1/account/me"):
    """A request whose url/method are real strings.

    A bare MagicMock makes `request.url.path.rstrip('/').lower()` a MagicMock,
    which `is_path_exempt` treats as exempt — the admission logic then returns
    at the passthrough and never reaches the ceiling gate under test.
    """
    req = MagicMock()
    req.url.path = path
    req.method = "GET"
    req.headers = {}
    req.state = MagicMock()
    req.client = ("10.0.0.1", 1234)
    return req


class _Recorder:
    """Captures the ctx the aggregate ceiling is evaluated against."""

    def __init__(self) -> None:
        self.aggregate_called = 0

    async def evaluate(self, request):
        svc = RequestAdmissionService()
        ctx = _ctx(user_id="revoked-user", token="tok")

        async def _resolve(request, c):
            # Simulate the downgrade: local rebind only, as the real code did.
            return 10, 60, "anonymous:key", True

        with (
            patch.object(svc, "_get_rate_limit_config", AsyncMock(return_value=ctx)),
            patch.object(svc, "_resolve_legacy_config", _resolve),
            patch("src.app.core.security.request_admission.settings") as settings,
            patch(
                "src.app.core.security.request_admission.get_ban_info",
                AsyncMock(return_value=MagicMock(is_banned=False)),
            ),
            patch("src.app.core.security.request_admission.consume", AsyncMock(return_value=(False, 9, 60, False))),
            patch.object(
                svc,
                "_check_anonymous_aggregate",
                AsyncMock(side_effect=self._aggregate),
            ),
        ):
            settings.security.AUTO_BAN_ENABLED = False
            # Must be False: left as a MagicMock attribute it is TRUTHY, so the
            # whitelist branch returns `admit` before the ceiling gate is ever
            # reached — a silent no-op that would make this test lie.
            settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
            settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = (100, 3600)
            await svc.evaluate(_request())

        return self

    async def _aggregate(self, client_ip):
        self.aggregate_called += 1
        return None


@pytest.mark.asyncio
async def test_downgraded_caller_still_faces_the_anonymous_ceiling() -> None:
    """The regression: the ceiling must be consulted for a downgraded caller."""
    rec = await _Recorder().evaluate(MagicMock())
    assert rec.aggregate_called == 1, "downgraded caller skipped the anonymous aggregate ceiling"


@pytest.mark.asyncio
async def test_genuinely_authenticated_caller_still_skips_it() -> None:
    """No regression: a real authenticated caller is covered by its tier budget."""
    rec = _Recorder()

    async def _resolve_authed(request, c):
        return 10, 60, "user:key", False  # not downgraded

    svc = RequestAdmissionService()
    ctx = _ctx(user_id="real-user", token="tok")
    with (
        patch.object(svc, "_get_rate_limit_config", AsyncMock(return_value=ctx)),
        patch.object(svc, "_resolve_legacy_config", _resolve_authed),
        patch("src.app.core.security.request_admission.settings") as settings,
        patch(
            "src.app.core.security.request_admission.get_ban_info",
            AsyncMock(return_value=MagicMock(is_banned=False)),
        ),
        patch("src.app.core.security.request_admission.consume", AsyncMock(return_value=(False, 9, 60, False))),
        patch.object(svc, "_check_anonymous_aggregate", AsyncMock(side_effect=rec._aggregate)),
    ):
        settings.security.AUTO_BAN_ENABLED = False
        settings.security.IP_WHITELIST_BYPASS_RATE_LIMIT = False
        settings.rate_limit.ANONYMOUS_IP_AGGREGATE_LIMIT = (100, 3600)
        await svc.evaluate(_request())

    assert rec.aggregate_called == 0, "an authenticated caller must keep its documented bypass"
