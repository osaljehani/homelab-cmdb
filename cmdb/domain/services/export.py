"""Whole-DB export / restore as a portable JSON-able dict.

This is portability and inspection, not backup — `cp data/cmdb.db` is the
backup. Tables are walked generically via ``Base.metadata.sorted_tables`` so
new columns and tables ride along without touching this module. Restore only
targets an **empty, already-migrated** database (``force=True`` wipes first);
there are no merge semantics.
"""

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from cmdb.domain.models import Base

EXPORT_VERSION = 1


def _current_revision(session: Session) -> str | None:
    try:
        row = session.execute(sa.text("SELECT version_num FROM alembic_version"))
        return row.scalar()
    except sa.exc.OperationalError:
        return None  # no alembic_version table (e.g. metadata-created test DB)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def export_all(session: Session) -> dict:
    """Dump every table to ``{"version", "alembic_revision", "exported_at",
    "tables": {name: [row dicts]}}``. Datetimes become ISO strings; JSON
    columns pass through as objects; enum columns keep their stored strings."""
    tables: dict[str, list[dict]] = {}
    for table in Base.metadata.sorted_tables:
        rows = session.execute(table.select()).mappings().all()
        # str() strips SQLAlchemy's quoted_name str-subclass, which yaml.safe_dump rejects.
        tables[str(table.name)] = [
            {str(k): _serialize(v) for k, v in row.items()} for row in rows
        ]
    return {
        "version": EXPORT_VERSION,
        "alembic_revision": _current_revision(session),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables,
    }


def _parse_row(table: sa.Table, row: dict) -> dict:
    out = {}
    for key, value in row.items():
        col = table.columns.get(key)
        if col is None:
            continue  # column unknown to this schema; revision check should prevent this
        if value is not None and isinstance(col.type, sa.DateTime):
            value = datetime.fromisoformat(value)
        out[key] = value
    return out


def restore_all(session: Session, data: dict, force: bool = False) -> dict[str, int]:
    """Load an :func:`export_all` dump into an empty, migrated database.

    Primary keys are preserved (so FKs stay valid). Raises ValueError when the
    dump's alembic revision differs from the database's, or when any table
    already has rows and ``force`` is False. ``force=True`` deletes everything
    first. Returns per-table inserted counts.
    """
    if data.get("version") != EXPORT_VERSION:
        raise ValueError(f"unsupported export version: {data.get('version')!r}")

    db_rev = _current_revision(session)
    dump_rev = data.get("alembic_revision")
    if dump_rev != db_rev:
        raise ValueError(
            f"alembic revision mismatch: dump is {dump_rev!r}, database is "
            f"{db_rev!r} — migrate the target (cmdb db upgrade) or re-export"
        )

    tables = data.get("tables") or {}

    if force:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
    else:
        for table in Base.metadata.sorted_tables:
            count = session.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar()
            if count:
                raise ValueError(
                    f"target database is not empty (table '{table.name}' has "
                    f"{count} rows) — pass force to wipe and restore"
                )

    counts: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        rows = tables.get(table.name) or []
        parsed = [_parse_row(table, r) for r in rows]
        if parsed:
            session.execute(table.insert(), parsed)
        counts[table.name] = len(parsed)
    session.flush()
    return counts
