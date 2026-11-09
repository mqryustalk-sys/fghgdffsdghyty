"""Initial persistent paper ledger."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_records_kind", "records", ["kind"])
    op.create_index("ix_records_at", "records", ["at"])
    op.create_table("state", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("data", sa.JSON(), nullable=False))
    op.create_table(
        "baskets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("signal_id", sa.String(), sa.ForeignKey("records.id"), nullable=False, unique=True),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_baskets_at", "baskets", ["at"])
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("basket_id", sa.String(), sa.ForeignKey("baskets.id"), nullable=False),
        sa.Column("at", sa.Float(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_orders_basket_id", "orders", ["basket_id"])


def downgrade():
    for name in ("orders", "baskets", "state", "records"):
        op.drop_table(name)
