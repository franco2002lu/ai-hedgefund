"""add attribution reports

Revision ID: b91f2a6c3d44
Revises: 5c8e7c02dde9
Create Date: 2026-06-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b91f2a6c3d44"
down_revision: Union[str, None] = "5c8e7c02dde9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attribution_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("branch_name", sa.String(length=50), nullable=False),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("basket_return_conviction", sa.Numeric(10, 6), nullable=False),
        sa.Column("basket_return_equal", sa.Numeric(10, 6), nullable=False),
        sa.Column("benchmark_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("benchmark_symbol", sa.String(length=10), nullable=False),
        sa.Column("spy_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("analyst_ics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n_holdings", sa.Integer(), nullable=False),
        sa.Column("n_holdings_priced", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("branch_id", "decision_date", name="uq_attribution_branch_decision"),
    )


def downgrade() -> None:
    op.drop_table("attribution_reports")
