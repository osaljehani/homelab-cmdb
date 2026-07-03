"""canonicalize image refs (dedup runtime vs registry scans)

Revision ID: e3a4b5c6d7f8
Revises: d2f3a4b5c6e7
Create Date: 2026-07-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from cmdb.domain.refs import canonical_ref

revision: str = "e3a4b5c6d7f8"
down_revision: Union[str, Sequence[str], None] = "d2f3a4b5c6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def canonicalize_refs(bind) -> None:
    """Rewrite every images.ref to its canonical form, merging collisions.

    Deterministic survivor = smallest id. Duplicate scans are reassigned to
    the survivor; the survivor's timestamps/digest roll up to the newest scan.
    Idempotent: once all refs are canonical, re-running changes nothing.
    """
    rows = list(
        bind.execute(
            sa.text("SELECT id, ref, digest, first_seen, last_scanned_at FROM images")
        )
    )
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(canonical_ref(r.ref), []).append(r)

    for canon, members in groups.items():
        members.sort(key=lambda r: r.id)
        survivor = members[0]

        for dup in members[1:]:
            bind.execute(
                sa.text("UPDATE image_scans SET image_id=:sid WHERE image_id=:did"),
                {"sid": survivor.id, "did": dup.id},
            )
            bind.execute(sa.text("DELETE FROM images WHERE id=:did"), {"did": dup.id})

        first_seen = min(
            (m.first_seen for m in members if m.first_seen),
            default=survivor.first_seen,
        )
        scanned = [m for m in members if m.last_scanned_at]
        newest = max(scanned, key=lambda m: m.last_scanned_at) if scanned else survivor
        bind.execute(
            sa.text(
                "UPDATE images SET ref=:ref, first_seen=:fs, "
                "last_scanned_at=:ls, digest=:dg WHERE id=:id"
            ),
            {
                "ref": canon,
                "fs": first_seen,
                "ls": newest.last_scanned_at,
                "dg": newest.digest,
                "id": survivor.id,
            },
        )


def upgrade() -> None:
    canonicalize_refs(op.get_bind())


def downgrade() -> None:
    # Non-reversible: original per-source ref spellings are not recoverable.
    pass
