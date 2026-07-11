from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cmdb.domain.models import Base, Host, Image, ImageScan, ImportLog, ImportSource
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.export import export_all, restore_all
from cmdb.domain.services.hosts import add_tag, set_custom_field


@pytest.fixture
def db2():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _populate(db, host_facts):
    import_host(db, host_facts)
    add_tag(db, "testhost", "lab")
    set_custom_field(db, "testhost", "owner", "alice")
    img = Image(ref="nginx:latest", first_seen=datetime(2026, 7, 1))
    db.add(img)
    db.flush()
    db.add(
        ImageScan(
            image_id=img.id,
            scanned_at=datetime(2026, 7, 3, 4, 0),
            source="docker",
            host="testhost",
            total=0,
        )
    )
    db.add(ImportLog(source=ImportSource.CLI, filename="seed"))
    db.flush()


def test_export_shape(db, host_facts):
    _populate(db, host_facts)
    dump = export_all(db)
    assert dump["version"] == 1
    assert "exported_at" in dump
    assert {"hosts", "images", "image_scans", "tags", "import_logs"} <= set(
        dump["tables"]
    )
    host_row = dump["tables"]["hosts"][0]
    assert host_row["hostname"] == "testhost"
    assert host_row["custom_fields"] == {"owner": "alice"}  # JSON column intact
    assert isinstance(host_row["last_seen"], str)  # datetime serialized


def test_round_trip_into_empty_db(db, db2, host_facts):
    _populate(db, host_facts)
    dump = export_all(db)

    counts = restore_all(db2, dump)

    assert counts["hosts"] == 1
    host = db2.query(Host).one()
    assert host.hostname == "testhost"
    assert host.custom_fields == {"owner": "alice"}
    assert [t.name for t in host.tags] == ["lab"]
    assert isinstance(host.last_seen, datetime)  # datetime restored, not str
    scan = db2.query(ImageScan).one()
    assert scan.scanned_at == datetime(2026, 7, 3, 4, 0)
    assert scan.image.ref == "nginx:latest"
    # enum column round-trips
    assert db2.query(ImportLog).one().source == ImportSource.CLI


def test_restore_refuses_non_empty_without_force(db, db2, host_facts):
    _populate(db, host_facts)
    dump = export_all(db)
    restore_all(db2, dump)
    with pytest.raises(ValueError, match="not empty"):
        restore_all(db2, dump)


def test_restore_force_wipes_first(db, db2, host_facts):
    _populate(db, host_facts)
    dump = export_all(db)
    restore_all(db2, dump)
    counts = restore_all(db2, dump, force=True)
    assert counts["hosts"] == 1
    assert db2.query(Host).count() == 1


def test_restore_revision_mismatch_raises(db, db2, host_facts):
    _populate(db, host_facts)
    dump = export_all(db)
    dump["alembic_revision"] = "0123456789ab"
    with pytest.raises(ValueError, match="revision"):
        restore_all(db2, dump)
