"""add images, image_scans, vulnerabilities + import_log scan columns

Revision ID: c1a2b3d4e5f6
Revises: b7e4d9c1a2f3
Create Date: 2026-07-01

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b7e4d9c1a2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=True),
        sa.Column(
            "expected_noisy", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("first_seen", sa.DateTime(), nullable=True),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ref"),
    )
    op.create_index("ix_images_ref", "images", ["ref"])
    op.create_table(
        "image_scans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(), nullable=False),
        sa.Column("trivy_version", sa.String(), nullable=True),
        sa.Column("import_log_id", sa.Integer(), nullable=True),
        sa.Column("critical", sa.Integer(), nullable=True),
        sa.Column("high", sa.Integer(), nullable=True),
        sa.Column("medium", sa.Integer(), nullable=True),
        sa.Column("low", sa.Integer(), nullable=True),
        sa.Column("unknown", sa.Integer(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["images.id"],
        ),
        sa.ForeignKeyConstraint(
            ["import_log_id"],
            ["import_logs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "vulnerabilities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("vuln_id", sa.String(), nullable=False),
        sa.Column("pkg_name", sa.String(), nullable=True),
        sa.Column("installed_version", sa.String(), nullable=True),
        sa.Column("fixed_version", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("target", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["image_scans.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "import_logs", sa.Column("images_scanned", sa.Integer(), nullable=True)
    )
    op.add_column(
        "import_logs",
        sa.Column("vulnerabilities_upserted", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_logs", "vulnerabilities_upserted")
    op.drop_column("import_logs", "images_scanned")
    op.drop_table("vulnerabilities")
    op.drop_table("image_scans")
    op.drop_index("ix_images_ref", table_name="images")
    op.drop_table("images")
