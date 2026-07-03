"""add image_scans.source (docker vs kubernetes provenance)

Revision ID: d2f3a4b5c6e7
Revises: c1a2b3d4e5f6
Create Date: 2026-07-03

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2f3a4b5c6e7"
down_revision: Union[str, Sequence[str], None] = "c1a2b3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_scans", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("image_scans", "source")
