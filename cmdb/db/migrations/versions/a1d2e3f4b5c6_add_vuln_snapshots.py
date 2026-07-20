"""add vuln_snapshots (immutable daily trend history) + backfill

Revision ID: a1d2e3f4b5c6
Revises: b8c9d0e1f2a3
Create Date: 2026-07-20

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from cmdb.domain.refs import canonical_ref

revision: str = "a1d2e3f4b5c6"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEVERITY_COLS = ("critical", "high", "medium", "low", "unknown", "total")


def backfill(bind) -> None:
    """Reconstruct daily snapshot rows from existing image_scans history.

    Raw SQL only (no ORM models — they would reference columns later
    migrations may not have added yet on a fresh-DB replay). Running/noisy
    flags come from *current* container/workload/image state — historical
    placement was never recorded, so this matches what the live trend showed
    at upgrade time. Idempotent: dates that already have rows are skipped.
    """
    running_refs = set()
    for image, state in bind.execute(
        sa.text("SELECT image, state FROM containers WHERE image IS NOT NULL")
    ):
        if state is None or state.lower() in ("running", "up"):
            running_refs.add(canonical_ref(image))
    running_refs.update(
        ref
        for (ref,) in bind.execute(
            sa.text(
                "SELECT DISTINCT image_canonical FROM k8s_workloads "
                "WHERE image_canonical IS NOT NULL"
            )
        )
    )

    images = {
        row.id: row
        for row in bind.execute(sa.text("SELECT id, ref, expected_noisy FROM images"))
    }
    scans = list(
        bind.execute(
            sa.text(
                "SELECT image_id, scanned_at, critical, high, medium, low, "
                "unknown, total FROM image_scans ORDER BY scanned_at"
            )
        )
    )
    if not scans:
        return

    existing_dates = {
        d
        for (d,) in bind.execute(
            sa.text("SELECT DISTINCT snapshot_date FROM vuln_snapshots")
        )
    }

    def scan_date(scanned_at) -> str:
        # SQLite hands back ISO strings; keep the date part as "YYYY-MM-DD".
        return str(scanned_at)[:10]

    for day in sorted({scan_date(s.scanned_at) for s in scans}):
        if day in existing_dates:
            continue
        latest = {}
        for s in scans:  # ascending order: last write wins
            if scan_date(s.scanned_at) <= day:
                latest[s.image_id] = s
        for image_id, scan in latest.items():
            image = images.get(image_id)
            if image is None:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO vuln_snapshots (snapshot_date, image_ref, "
                    "was_running, was_noisy, scanned_at, critical, high, "
                    "medium, low, unknown, total) VALUES (:day, :ref, :run, "
                    ":noisy, :ts, :critical, :high, :medium, :low, :unknown, "
                    ":total)"
                ),
                {
                    "day": day,
                    "ref": image.ref,
                    "run": image.ref in running_refs,
                    "noisy": bool(image.expected_noisy),
                    "ts": scan.scanned_at,
                    **{c: getattr(scan, c) or 0 for c in _SEVERITY_COLS},
                },
            )


def upgrade() -> None:
    op.create_table(
        "vuln_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False, index=True),
        sa.Column("image_ref", sa.String(), nullable=False),
        sa.Column(
            "was_running", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("was_noisy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scanned_at", sa.DateTime(), nullable=True),
        sa.Column("critical", sa.Integer(), server_default="0"),
        sa.Column("high", sa.Integer(), server_default="0"),
        sa.Column("medium", sa.Integer(), server_default="0"),
        sa.Column("low", sa.Integer(), server_default="0"),
        sa.Column("unknown", sa.Integer(), server_default="0"),
        sa.Column("total", sa.Integer(), server_default="0"),
        sa.UniqueConstraint("snapshot_date", "image_ref"),
    )
    backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("vuln_snapshots")
