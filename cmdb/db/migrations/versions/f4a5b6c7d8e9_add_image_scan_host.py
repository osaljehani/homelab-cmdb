"""add image_scans.host (raw envelope provenance label)

Revision ID: f4a5b6c7d8e9
Revises: e3a4b5c6d7f8
Create Date: 2026-07-11

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3a4b5c6d7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("image_scans", sa.Column("host", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("image_scans", "host")
