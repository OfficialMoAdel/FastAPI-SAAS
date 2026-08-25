"""partial_unique_indexes_for_stripe_ids

Revision ID: 11_stripe_partial_unique
Revises: 10_force_rls_and_bypass
Create Date: 2026-08-25

Adds partial unique indexes so Stripe identifiers cannot be attached to more
than one row, while allowing multiple NULLs (rows not yet linked to Stripe).

Downgrade drops the indexes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "11_stripe_partial_unique"
down_revision: str | None = "10_force_rls_and_bypass"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add partial unique indexes on Stripe external IDs."""
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_subscriptions_stripe_sub
        ON user_subscriptions(stripe_subscription_id)
        WHERE stripe_subscription_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_billing_records_stripe_invoice
        ON billing_records(stripe_invoice_id)
        WHERE stripe_invoice_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_billing_records_stripe_refund
        ON billing_records(stripe_refund_id)
        WHERE stripe_refund_id IS NOT NULL
        """
    )


def downgrade() -> None:
    """Drop the partial unique indexes."""
    op.drop_index("uq_billing_records_stripe_refund", table_name="billing_records")
    op.drop_index("uq_billing_records_stripe_invoice", table_name="billing_records")
    op.drop_index("uq_user_subscriptions_stripe_sub", table_name="user_subscriptions")
