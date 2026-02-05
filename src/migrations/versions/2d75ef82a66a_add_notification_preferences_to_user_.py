"""add_notification_preferences_to_user_preferences

Revision ID: 2d75ef82a66a
Revises: 2bebc73a18eb
Create Date: 2026-02-05 18:32:47.682211

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "2d75ef82a66a"
down_revision: Union[str, None] = "2bebc73a18eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_preferences",
        sa.Column("email_notifications_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "user_preferences", sa.Column("push_notifications_enabled", sa.Boolean(), nullable=False, server_default="true")
    )
    op.add_column(
        "user_preferences",
        sa.Column("disabled_notification_types", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "user_preferences", sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column("user_preferences", sa.Column("quiet_hours_start", sa.String(5), nullable=True))
    op.add_column("user_preferences", sa.Column("quiet_hours_end", sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column("user_preferences", "quiet_hours_end")
    op.drop_column("user_preferences", "quiet_hours_start")
    op.drop_column("user_preferences", "quiet_hours_enabled")
    op.drop_column("user_preferences", "disabled_notification_types")
    op.drop_column("user_preferences", "push_notifications_enabled")
    op.drop_column("user_preferences", "email_notifications_enabled")
