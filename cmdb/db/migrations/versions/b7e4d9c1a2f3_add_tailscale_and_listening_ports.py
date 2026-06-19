"""add tailscale columns, tailscale_services, listening_ports

Revision ID: b7e4d9c1a2f3
Revises: 50f23d7d5a7c
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7e4d9c1a2f3'
down_revision: Union[str, Sequence[str], None] = '50f23d7d5a7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hosts', sa.Column('tailscale_ipv4', sa.String(), nullable=True))
    op.add_column('hosts', sa.Column('tailscale_dns_name', sa.String(), nullable=True))
    op.add_column('hosts', sa.Column('tailscale_tags', sa.String(), nullable=True))
    op.add_column('hosts', sa.Column('tailscale_exit_node', sa.Boolean(), nullable=True))
    op.add_column('hosts', sa.Column('tailscale_online', sa.Boolean(), nullable=True))
    op.add_column('import_logs', sa.Column('tailscale_services_upserted', sa.Integer(), nullable=True))
    op.add_column('import_logs', sa.Column('listening_ports_upserted', sa.Integer(), nullable=True))
    op.create_table(
        'tailscale_services',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('proto', sa.String(), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('target', sa.String(), nullable=True),
        sa.Column('funnel', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'listening_ports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('proto', sa.String(), nullable=True),
        sa.Column('address', sa.String(), nullable=True),
        sa.Column('port', sa.Integer(), nullable=True),
        sa.Column('process', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['host_id'], ['hosts.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('listening_ports')
    op.drop_table('tailscale_services')
    op.drop_column('import_logs', 'listening_ports_upserted')
    op.drop_column('import_logs', 'tailscale_services_upserted')
    op.drop_column('hosts', 'tailscale_online')
    op.drop_column('hosts', 'tailscale_exit_node')
    op.drop_column('hosts', 'tailscale_tags')
    op.drop_column('hosts', 'tailscale_dns_name')
    op.drop_column('hosts', 'tailscale_ipv4')
