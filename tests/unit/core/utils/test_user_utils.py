"""Unit tests for get_user_attr and canonical shape utilities (Candidate 4)."""

from src.app.core.utils.user_utils import get_user_attr


class DummyUser:
    def __init__(self, id: int, username: str):
        self.id = id
        self.username = username


def test_get_user_attr_dict():
    data = {"id": 1, "username": "alice", "is_active": True}
    assert get_user_attr(data, "id") == 1
    assert get_user_attr(data, "username") == "alice"
    assert get_user_attr(data, "is_active") is True
    assert get_user_attr(data, "missing") is None
    assert get_user_attr(data, "missing", "default") == "default"


def test_get_user_attr_object():
    user = DummyUser(2, "bob")
    assert get_user_attr(user, "id") == 2
    assert get_user_attr(user, "username") == "bob"
    assert get_user_attr(user, "missing") is None
    assert get_user_attr(user, "missing", 42) == 42


def test_get_user_attr_none():
    assert get_user_attr(None, "id") is None
    assert get_user_attr(None, "id", 99) == 99
