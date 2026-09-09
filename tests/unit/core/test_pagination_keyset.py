"""Unit tests for keyset (cursor) pagination helpers — core/pagination.py (P-M2).

Covers the cursor round-trip contract, malformed-cursor rejection, the
strict-keyset WHERE clause shape, and the keyset response envelope.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.app.core.pagination import (
    build_keyset_response,
    decode_keyset_cursor,
    encode_keyset_cursor,
    keyset_pagination_clause,
    paginate_keyset,
)
from src.app.modules.posts.models import Post


def _ts(minutes: int) -> datetime:
    return datetime(2026, 9, 7, 12, minutes, 0, tzinfo=UTC)


class TestCursorRoundTrip:
    """Opaque cursor encode/decode contract."""

    def test_int_id_round_trip(self) -> None:
        cursor = encode_keyset_cursor(_ts(0), 42)
        created_at, row_id = decode_keyset_cursor(cursor)
        assert created_at == _ts(0)
        # numeric ids decode as ints so they bind against Integer columns
        assert row_id == 42
        assert isinstance(row_id, int)

    def test_str_id_round_trip(self) -> None:
        cursor = encode_keyset_cursor(_ts(30), "a1b2c3")
        created_at, row_id = decode_keyset_cursor(cursor)
        assert created_at == _ts(30)
        assert row_id == "a1b2c3"

    def test_cursor_is_opaque_base64(self) -> None:
        """The token must not leak a client-visible format contract."""
        cursor = encode_keyset_cursor(_ts(0), 7)
        assert "|" not in cursor
        assert " " not in cursor
        # URL-safe alphabet only
        import base64 as b64

        b64.urlsafe_b64decode(cursor.encode("ascii"))  # does not raise

    @pytest.mark.parametrize(
        "bad",
        [
            "!!!not-base64!!!",
            "",  # empty
            "bm90LXBpcGV8ZGVsaW1pdGVy",  # "not-pipe|delimited" in base64
        ],
    )
    def test_malformed_cursor_raises_value_error(self, bad: str) -> None:
        with pytest.raises(ValueError):
            decode_keyset_cursor(bad)


class TestKeysetClause:
    """The strict-keyset WHERE clause for (created_at DESC, id DESC) paging."""

    def test_clause_renders_rowrange_seek(self) -> None:
        cursor = (_ts(0), 10)
        clause = keyset_pagination_clause(Post.created_at, Post.id, cursor)
        rendered = str(clause)
        assert "created_at <" in rendered
        assert "id <" in rendered

    def test_clause_equality_tiebreak_on_id(self) -> None:
        """Equal timestamps must disambiguate via the id tie-breaker arm."""
        cursor = (_ts(0), 10)
        clause = str(keyset_pagination_clause(Post.created_at, Post.id, cursor))
        assert "created_at =" in clause


class TestPaginateKeyset:
    """Statement assembly: WHERE composition, ordering, limit."""

    def test_no_cursor_orders_desc_and_limits(self) -> None:
        stmt, decoded = paginate_keyset(select(Post), Post.created_at, Post.id, None, 10)
        compiled = str(stmt)
        assert "ORDER BY" in compiled
        assert "DESC" in compiled
        assert "LIMIT 10" in compiled or "LIMIT :param" in compiled or "LIMIT" in compiled
        assert decoded is None

    def test_cursor_adds_where_clause(self) -> None:
        cursor = encode_keyset_cursor(_ts(0), 10)
        stmt, decoded = paginate_keyset(select(Post), Post.created_at, Post.id, cursor, 5)
        assert decoded is not None
        assert decoded[1] == 10
        assert "WHERE" in str(stmt)

    def test_cursor_composes_onto_existing_filters(self) -> None:
        base = select(Post).where(Post.is_deleted.is_(False))
        cursor = encode_keyset_cursor(_ts(0), 10)
        stmt, _ = paginate_keyset(base, Post.created_at, Post.id, cursor, 5)
        assert "is_deleted" in str(stmt)


class TestBuildKeysetResponse:
    """The keyset envelope: next_cursor iff the page was full."""

    def test_full_page_yields_next_cursor(self) -> None:
        items = [{"id": i, "created_at": _ts(0)} for i in range(10)]
        resp = build_keyset_response(
            items,
            10,
            get_created_at=lambda r: r["created_at"],
            get_id=lambda r: r["id"],
        )
        assert resp["has_next"] is True
        created_at, row_id = decode_keyset_cursor(resp["next_cursor"])
        assert (created_at, row_id) == (_ts(0), 9)

    def test_short_page_is_end_of_listing(self) -> None:
        items = [{"id": i, "created_at": _ts(0)} for i in range(3)]
        resp = build_keyset_response(
            items,
            10,
            get_created_at=lambda r: r["created_at"],
            get_id=lambda r: r["id"],
        )
        assert resp["has_next"] is False
        assert resp["next_cursor"] is None

    def test_envelope_has_no_total(self) -> None:
        """No total — counting the remaining set is the O(N) cost keyset avoids."""
        resp = build_keyset_response([], 10, get_created_at=lambda r: r, get_id=lambda r: r)
        assert "total" not in resp
        assert "page" not in resp
