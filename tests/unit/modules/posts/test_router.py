"""Unit tests for posts/router.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

from src.app.modules.posts import router as posts_router


def _passthrough_cache(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


def _make_service():
    service = MagicMock()
    service.create_post = AsyncMock(return_value={"id": 1})
    service.get_posts = AsyncMock(return_value={"items": []})
    service.get_post = AsyncMock(return_value={"id": 1})
    service.update_post = AsyncMock(return_value={"message": "ok"})
    service.delete_post = AsyncMock()
    service.hard_delete = AsyncMock()
    return service


def _make_user(username="alice", user_id=1):
    return {"username": username, "id": user_id}


def _make_ctx(username="alice", user_id=1):
    from unittest.mock import MagicMock

    user = {"username": username, "id": user_id}
    ctx = MagicMock()
    ctx.user = user
    ctx.user_id = user_id
    return ctx


async def test_write_post_success():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.write_post(
            request=MagicMock(), username="alice", post=MagicMock(), ctx=_make_ctx("alice"), service=service
        )
    service.create_post.assert_awaited_once()
    assert result == {"id": 1}


async def test_write_post_forbidden():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        with pytest.raises(Exception):
            await posts_router.write_post(
                request=MagicMock(), username="bob", post=MagicMock(), ctx=_make_ctx("alice"), service=service
            )
    service.create_post.assert_not_awaited()


async def test_read_posts_success():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.read_posts(
            request=MagicMock(), username="alice", ctx=_make_ctx("alice"), service=service, page=1, per_page=10
        )
    service.get_posts.assert_awaited_once_with(1, 1, 10)
    assert result == {"items": []}


async def test_read_posts_forbidden():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        with pytest.raises(Exception):
            await posts_router.read_posts(
                request=MagicMock(), username="bob", ctx=_make_ctx("alice"), service=service, page=1, per_page=10
            )


async def test_read_post_success():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.read_post(
            request=MagicMock(), username="alice", id=5, ctx=_make_ctx("alice"), service=service
        )
    service.get_post.assert_awaited_once_with(5, 1)
    assert result == {"id": 1}


async def test_read_post_forbidden():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        with pytest.raises(Exception):
            await posts_router.read_post(
                request=MagicMock(), username="bob", id=5, ctx=_make_ctx("alice"), service=service
            )


async def test_patch_post_success():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.patch_post(
            request=MagicMock(), username="alice", id=5, values=MagicMock(), ctx=_make_ctx("alice"), service=service
        )
    service.update_post.assert_awaited_once()
    assert result == {"message": "ok"}


async def test_patch_post_forbidden():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        with pytest.raises(Exception):
            await posts_router.patch_post(
                request=MagicMock(), username="bob", id=5, values=MagicMock(), ctx=_make_ctx("alice"), service=service
            )


async def test_erase_post_success():
    service = _make_service()
    user = _make_user("alice")
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.erase_post(
            request=MagicMock(), username="alice", id=5, ctx=_make_ctx("alice"), service=service
        )
    service.delete_post.assert_awaited_once_with(5, user)
    assert isinstance(result, Response)
    assert result.status_code == 204


async def test_erase_post_forbidden():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        with pytest.raises(Exception):
            await posts_router.erase_post(
                request=MagicMock(), username="bob", id=5, ctx=_make_ctx("alice"), service=service
            )


async def test_erase_db_post_success():
    service = _make_service()
    with patch.object(posts_router, "cache", _passthrough_cache):
        result = await posts_router.erase_db_post(request=MagicMock(), username="alice", id=5, service=service)
    service.hard_delete.assert_awaited_once_with(5)
    assert isinstance(result, Response)
    assert result.status_code == 204


async def test_get_post_service_dependency():
    db = MagicMock()
    with patch.object(posts_router, "get_post_service", return_value="svc") as gps:
        result = await posts_router._get_post_service(db=db)
    gps.assert_called_once_with(db)
    assert result == "svc"
