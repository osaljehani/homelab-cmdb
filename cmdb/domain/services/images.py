from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Image, ImageScan


def newest_scan_time(session: Session) -> datetime | None:
    """Timestamp of the most recent scan run across all images (None if unscanned)."""
    return session.query(func.max(ImageScan.scanned_at)).scalar()


def is_stale(image: Image, newest: datetime | None) -> bool:
    """True when the image was not part of the newest scan run.

    A never-scanned image (no ``last_scanned_at``) is "not scanned", not stale.
    """
    if newest is None or image.last_scanned_at is None:
        return False
    return image.last_scanned_at < newest


def list_images(session: Session, include_noisy: bool = True) -> list[Image]:
    q = session.query(Image).order_by(Image.ref)
    if not include_noisy:
        q = q.filter(Image.expected_noisy.is_(False))
    return q.all()


def get_image(session: Session, ref: str) -> Image | None:
    return session.query(Image).filter(Image.ref == ref).first()


def latest_scan(session: Session, image: Image) -> ImageScan | None:
    return (
        session.query(ImageScan)
        .filter(ImageScan.image_id == image.id)
        .order_by(ImageScan.scanned_at.desc())
        .first()
    )


def set_noisy(session: Session, ref: str, value: bool) -> Image:
    image = get_image(session, ref)
    if image is None:
        raise ValueError(f"Image '{ref}' not found")
    image.expected_noisy = value
    session.flush()
    return image


def delete_image(session: Session, ref: str) -> dict:
    """Delete an image and its entire scan history (scans + vulnerabilities).

    Returns the counts removed. Raises ValueError if the image is unknown.
    The ORM cascade (Image -> scans -> vulnerabilities) handles the children.
    """
    image = get_image(session, ref)
    if image is None:
        raise ValueError(f"Image '{ref}' not found")
    scans = len(image.scans)
    vulns = sum(len(scan.vulnerabilities) for scan in image.scans)
    session.delete(image)
    session.flush()
    return {"ref": ref, "scans": scans, "vulnerabilities": vulns}


def vuln_summary(session: Session) -> dict:
    """Rollup over the latest scan per non-noisy image."""
    images = list_images(session, include_noisy=False)
    out = {
        "images": len(images),
        "scanned_images": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0,
        "total": 0,
    }
    for image in images:
        scan = latest_scan(session, image)
        if scan is None:
            continue
        out["scanned_images"] += 1
        for key in ("critical", "high", "medium", "low", "unknown", "total"):
            out[key] += getattr(scan, key) or 0
    return out
