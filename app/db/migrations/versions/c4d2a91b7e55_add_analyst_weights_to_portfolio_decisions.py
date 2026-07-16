"""add analyst_weights to portfolio_decisions

Revision ID: c4d2a91b7e55
Revises: b91f2a6c3d44
Create Date: 2026-07-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d2a91b7e55"
down_revision: Union[str, None] = "b91f2a6c3d44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "portfolio_decisions",
        sa.Column("analyst_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portfolio_decisions", "analyst_weights")
