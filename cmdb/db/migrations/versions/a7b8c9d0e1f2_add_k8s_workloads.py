"""add k8s_workloads (pod -> image placements) + import counter

Revision ID: a7b8c9d0e1f2
Revises: f4a5b6c7d8e9
Create Date: 2026-07-11

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "k8s_workloads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("pod_name", sa.String(), nullable=False),
        sa.Column("container_name", sa.String(), nullable=False),
        sa.Column("image", sa.String(), nullable=False),
        sa.Column("image_canonical", sa.String(), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["k8s_clusters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "namespace", "pod_name", "container_name"),
    )
    op.create_index(
        "ix_k8s_workloads_image_canonical",
        "k8s_workloads",
        ["image_canonical"],
    )
    op.add_column(
        "import_logs", sa.Column("k8s_workloads_upserted", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("import_logs", "k8s_workloads_upserted")
    op.drop_index("ix_k8s_workloads_image_canonical", table_name="k8s_workloads")
    op.drop_table("k8s_workloads")
