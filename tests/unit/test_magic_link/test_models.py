"""Unit tests for MagicLink model.

TDD: These tests are written first and should FAIL until implementation is complete.

Task: T019 [US1] Write unit tests for MagicLink model
"""


from sqlalchemy import inspect


class TestMagicLinkModel:
    """Test MagicLink SQLAlchemy model."""

    def test_magic_link_model_exists(self):
        """Verify MagicLink model can be imported."""
        from src.app.modules.magic_link.models import MagicLink

        assert MagicLink is not None

    def test_magic_link_table_name(self):
        """Verify table name is 'magic_links'."""
        from src.app.modules.magic_link.models import MagicLink

        assert MagicLink.__tablename__ == "magic_links"

    def test_magic_link_has_required_columns(self):
        """Verify all required columns exist per data-model.md."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        column_names = [col.key for col in mapper.columns]

        required_columns = [
            "id",
            "user_id",
            "token",
            "action",
            "expires_at",
            "used_at",
            "ip_address",
            "user_agent",
            "created_at",
        ]

        for col in required_columns:
            assert col in column_names, f"Missing required column: {col}"

    def test_magic_link_token_column_constraints(self):
        """Verify token column has correct constraints."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        token_col = mapper.columns["token"]

        # Token should be nullable=False and have unique index
        assert not token_col.nullable
        assert token_col.type.length == 64  # 43 base64 chars + buffer

    def test_magic_link_user_relationship(self):
        """Verify relationship to User model exists."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        relationships = [r.key for r in mapper.relationships]

        assert "user" in relationships

    def test_magic_link_user_id_foreign_key(self):
        """Verify user_id has correct foreign key constraint."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        user_id_col = mapper.columns["user_id"]

        # Check foreign key
        fks = list(user_id_col.foreign_keys)
        assert len(fks) == 1
        assert "user.id" in str(fks[0].target_fullname)

    def test_magic_link_expires_at_required(self):
        """Verify expires_at is required (non-nullable)."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        expires_at_col = mapper.columns["expires_at"]

        assert not expires_at_col.nullable

    def test_magic_link_used_at_nullable(self):
        """Verify used_at is nullable (only set when token is consumed)."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        used_at_col = mapper.columns["used_at"]

        assert used_at_col.nullable

    def test_magic_link_ip_address_inet_type(self):
        """Verify ip_address uses PostgreSQL INET type."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        ip_col = mapper.columns["ip_address"]

        # Check that it's using INET type
        assert "INET" in str(ip_col.type).upper()

    def test_magic_link_action_max_length(self):
        """Verify action column has appropriate max length."""
        from src.app.modules.magic_link.models import MagicLink

        mapper = inspect(MagicLink)
        action_col = mapper.columns["action"]

        assert action_col.type.length >= 50  # Per data-model.md
