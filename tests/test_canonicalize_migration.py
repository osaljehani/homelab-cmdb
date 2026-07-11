from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan
from cmdb.db.migrations.versions.e3a4b5c6d7f8_canonicalize_image_refs import (
    canonicalize_refs,
)


def _img(db, ref, digest, scanned_at, noisy=False):
    img = Image(
        ref=ref,
        digest=digest,
        first_seen=scanned_at,
        last_scanned_at=scanned_at,
        expected_noisy=noisy,
    )
    db.add(img)
    db.flush()
    db.add(ImageScan(image_id=img.id, scanned_at=scanned_at, total=0, source="docker"))
    db.flush()
    return img


def test_migration_merges_colliding_refs(db: Session):
    # Two rows that canonicalize to the same ref, each with one scan.
    runtime = _img(
        db, "memcached:1.6.29-alpine", "memcached@sha256:d1", datetime(2026, 7, 1)
    )
    # registry row gets a larger id (created second), so runtime is the survivor.
    _img(
        db,
        "library/memcached:1.6.29-alpine",
        "library/memcached@sha256:d1",
        datetime(2026, 7, 2),
    )
    runtime_id = runtime.id

    canonicalize_refs(db.connection())
    db.expire_all()

    imgs = db.query(Image).all()
    assert len(imgs) == 1  # merged into one survivor
    assert imgs[0].id == runtime_id  # smaller id survives
    assert imgs[0].ref == "memcached:1.6.29-alpine"
    assert (
        db.query(ImageScan).filter_by(image_id=imgs[0].id).count() == 2
    )  # both scans moved
    assert imgs[0].last_scanned_at == datetime(2026, 7, 2)  # newest rolled up


def test_migration_rewrites_singleton_ref(db: Session):
    _img(db, "homelabcmdb-cmdb", None, datetime(2026, 7, 1))  # tagless
    canonicalize_refs(db.connection())
    db.expire_all()
    assert db.query(Image).one().ref == "homelabcmdb-cmdb:latest"


def test_migration_rolls_up_noisy_flag_from_duplicate(db: Session):
    # Survivor (smaller id) is clean; the merged-away dup is noisy.
    _img(db, "noisy:1", None, datetime(2026, 7, 1))
    _img(db, "library/noisy:1", None, datetime(2026, 7, 2), noisy=True)

    canonicalize_refs(db.connection())
    db.expire_all()

    assert db.query(Image).one().expected_noisy is True


def test_migration_keeps_noisy_survivor(db: Session):
    _img(db, "noisy:1", None, datetime(2026, 7, 1), noisy=True)
    _img(db, "library/noisy:1", None, datetime(2026, 7, 2))

    canonicalize_refs(db.connection())
    db.expire_all()

    assert db.query(Image).one().expected_noisy is True


def test_migration_is_idempotent(db: Session):
    _img(db, "library/memcached:1.6.29-alpine", None, datetime(2026, 7, 1))
    canonicalize_refs(db.connection())
    canonicalize_refs(db.connection())  # second run is a no-op
    db.expire_all()
    assert db.query(Image).one().ref == "memcached:1.6.29-alpine"
