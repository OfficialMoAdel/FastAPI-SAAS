"""Unit tests for api/deps/core.py rate_limiter_dependency."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from src.app.api.deps import core as deps_core
from src.app.core.exceptions.http_exceptions import RateLimitException


def _make_request(path="/api/v1/foo", method="GET", client_host="1.2.3.4", body=None):
    request = MagicMock(spec=Request)
    request.url.path = path
    request.method = method
    request.client = MagicMock()
    request.client.host = client_host
    request.app.state = MagicMock(spec=object)
    request.json = AsyncMock(return_value=body or {})
    return request


def _fake_settings():
    s = MagicMock()
    s.DEFAULT_RATE_LIMIT_LIMIT = 50
    s.DEFAULT_RATE_LIMIT_PERIOD = 30
    return s


async def test_rate_limiter_user_with_tier_and_limit():
    request = _make_request()
    db = MagicMock()
    user = {"id": "u1", "tier": {"id": "t1", "name": "pro"}}
    rate_limit = {"limit": 100, "period": 60}
    with (
        patch.object(deps_core, "crud_rate_limits") as crud,
        patch.object(deps_core, "rate_limiter") as rl,
    ):
        crud.get = AsyncMock(return_value=rate_limit)
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=user)
    crud.get.assert_awaited_once()
    kwargs = rl.is_rate_limited.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["limit"] == 100
    assert kwargs["period"] == 60


async def test_rate_limiter_user_with_tier_no_limit_uses_default():
    request = _make_request()
    db = MagicMock()
    user = {"id": "u1", "tier": {"id": "t1", "name": "pro"}}
    with (
        patch.object(deps_core, "crud_rate_limits") as crud,
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        crud.get = AsyncMock(return_value=None)
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=user)
    kwargs = rl.is_rate_limited.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["limit"] == 50
    assert kwargs["period"] == 30


async def test_rate_limiter_user_no_tier_uses_default():
    request = _make_request()
    db = MagicMock()
    user = {"id": "u1"}
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=user)
    kwargs = rl.is_rate_limited.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["limit"] == 50
    assert kwargs["period"] == 30


async def test_rate_limiter_anonymous_with_email():
    request = _make_request(method="POST", body={"email": "a@b.com"})
    db = MagicMock()
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=None)
    user_id = rl.is_rate_limited.call_args.kwargs["user_id"]
    assert user_id.startswith("email:")


async def test_rate_limiter_anonymous_no_email_uses_host():
    request = _make_request(method="POST", body={}, client_host="9.9.9.9")
    db = MagicMock()
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=None)
    assert rl.is_rate_limited.call_args.kwargs["user_id"] == "unknown:9.9.9.9"


async def test_rate_limiter_anonymous_no_client_uses_unknown():
    request = _make_request(method="GET", client_host=None)
    request.client = None
    db = MagicMock()
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=None)
    assert rl.is_rate_limited.call_args.kwargs["user_id"] == "unknown"


async def test_rate_limiter_json_parse_error_falls_through():
    request = _make_request(method="POST", body={})
    request.json = AsyncMock(side_effect=ValueError("bad json"))
    request.client = None
    db = MagicMock()
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=None)
    assert rl.is_rate_limited.call_args.kwargs["user_id"] == "unknown"


async def test_rate_limiter_raises_when_limited():
    request = _make_request()
    db = MagicMock()
    user = {"id": "u1"}
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=True)
        with pytest.raises(RateLimitException):
            await deps_core.rate_limiter_dependency(request=request, db=db, user=user)


async def test_rate_limiter_waits_for_initialization():
    request = _make_request()
    event = AsyncMock()
    request.app.state.initialization_complete = event
    db = MagicMock()
    user = {"id": "u1"}
    with (
        patch.object(deps_core, "rate_limiter") as rl,
        patch.object(deps_core, "settings", _fake_settings()),
    ):
        rl.is_rate_limited = AsyncMock(return_value=False)
        await deps_core.rate_limiter_dependency(request=request, db=db, user=user)
    event.wait.assert_awaited_once()
