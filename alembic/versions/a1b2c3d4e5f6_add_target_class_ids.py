"""add target_class_ids to news

Revision ID: a1b2c3d4e5f6
Revises: 0f5e0bdac5c4
Create Date: 2026-06-28 12:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '0f5e0bdac5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('news', sa.Column(
        'target_class_ids',
        sa.Text(),
        nullable=True,
        comment='JSON list of class IDs for multi-class targeting',
    ))


def downgrade() -> None:
    op.drop_column('news', 'target_class_ids')
