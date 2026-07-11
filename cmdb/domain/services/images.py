from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Image, ImageScan, K8sWorkload
from cmdb.domain.refs import canonical_ref


def source_watermarks(session: Session) -> dict[str | None, datetime]:
    """Newest scan time per ``ImageScan.source`` (NULL source is its own bucket)."""
    rows = (
        session.query(ImageScan.source, func.max(ImageScan.scanned_at))
        .group_by(ImageScan.source)
        .all()
    )
    return dict(rows)


def is_stale(
    latest_by_source: dict[str | None, datetime],
    watermarks: dict[str | None, datetime],
) -> bool:
    """True when the image missed the newest run of every source it's known to.

    Staleness is per-source: a docker-scanned image is compared against the
    newest docker run, not against a (possibly newer) registry run. An image
    is current while at least one of its sources still includes it. A
    never-scanned image (empty ``latest_by_source``) is "not scanned", not
    stale.
    """
    if not latest_by_source:
        return False
    return all(ts < watermarks.get(src, ts) for src, ts in latest_by_source.items())


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


def _is_running(state: str | None) -> bool:
    # None counts as running: pre-state-column imports lack it (same
    # benefit-of-the-doubt convention as the containers page).
    return state is None or state.lower() in ("running", "up")


def container_placements(session: Session) -> dict[str, list[dict]]:
    """Canonical image ref -> running-container placements, in one pass.

    ``Image.ref`` is already canonical (from ingest/migration), so we
    canonicalize each container's raw ``docker ps`` ``.Image`` string, making
    the join tolerant of registry-host/``library/``/tag differences (e.g. a
    ``homelabcmdb-cmdb`` container vs a ``homelabcmdb-cmdb:latest`` image).
    """
    placements: dict[str, list[dict]] = {}
    for c in session.query(Container).all():
        if not c.image or not _is_running(c.state):
            continue
        placements.setdefault(canonical_ref(c.image), []).append(
            {"host": c.host.hostname if c.host else None, "name": c.name}
        )
    return placements


def k8s_placements(session: Session) -> dict[str, list[dict]]:
    """Canonical image ref -> k8s workload placements, in one pass.

    ``image_canonical`` is computed at import (NULL for digest-only refs), so
    the join to ``Image.ref`` mirrors :func:`container_placements`.
    """
    placements: dict[str, list[dict]] = {}
    for w in session.query(K8sWorkload).all():
        if not w.image_canonical:
            continue
        placements.setdefault(w.image_canonical, []).append(
            {
                "cluster": w.cluster.name if w.cluster else None,
                "namespace": w.namespace,
                "pod": w.pod_name,
                "container": w.container_name,
            }
        )
    return placements


def _deployment(
    image: Image, scan: ImageScan | None, placements: dict, k8s: dict
) -> dict:
    docker = placements.get(image.ref, [])
    kubernetes = k8s.get(image.ref, [])
    return {
        "docker": docker,
        "kubernetes": kubernetes,
        "source": scan.source if scan else None,
        "status": "running" if (docker or kubernetes) else "registry-only",
    }


def deployments(
    session: Session,
    image: Image,
    placements: dict | None = None,
    k8s: dict | None = None,
) -> dict:
    """Where an image is deployed.

    Docker placements come from :func:`container_placements` (running
    containers only), kubernetes placements from :func:`k8s_placements`
    (Running pods); pass precomputed maps when iterating many images. An image
    counts as running when either runtime references it.

    Returns ``{"docker": [{"host", "name"}, ...],
    "kubernetes": [{"cluster", "namespace", "pod", "container"}, ...],
    "source": <str|None>, "status": "running"|"registry-only"}``.
    """
    if placements is None:
        placements = container_placements(session)
    if k8s is None:
        k8s = k8s_placements(session)
    return _deployment(image, latest_scan(session, image), placements, k8s)


def _overview_row(
    image: Image,
    scan: ImageScan | None,
    latest_by_source: dict[str | None, datetime],
    watermarks: dict[str | None, datetime],
    placements: dict,
    k8s: dict,
) -> dict:
    dep = _deployment(image, scan, placements, k8s)
    return {
        "image": image,
        "scan": scan,
        "stale": is_stale(latest_by_source, watermarks),
        "status": dep["status"],
        "deployments": dep,
        "latest_by_source": latest_by_source,
    }


def image_overview(session: Session, include_noisy: bool = True) -> list[dict]:
    """Batched per-image rows for list surfaces (web, CLI, MCP).

    Four queries total regardless of image count: images, containers,
    per-source watermarks, and one scan pass reduced in Python to the latest
    scan per image plus the latest scan time per (image, source).

    Rows are sorted noisy-last, then running-first, critical desc, ref.
    """
    placements = container_placements(session)
    k8s = k8s_placements(session)
    watermarks = source_watermarks(session)
    latest_scan_by_image: dict[int, ImageScan] = {}
    latest_by_image_source: dict[int, dict[str | None, datetime]] = {}
    for scan in session.query(ImageScan).order_by(ImageScan.scanned_at).all():
        latest_scan_by_image[scan.image_id] = scan
        latest_by_image_source.setdefault(scan.image_id, {})[scan.source] = (
            scan.scanned_at
        )
    rows = [
        _overview_row(
            image,
            latest_scan_by_image.get(image.id),
            latest_by_image_source.get(image.id, {}),
            watermarks,
            placements,
            k8s,
        )
        for image in list_images(session, include_noisy=include_noisy)
    ]
    rows.sort(
        key=lambda r: (
            r["image"].expected_noisy,
            r["status"] != "running",
            -(r["scan"].critical if r["scan"] else 0),
            r["image"].ref,
        )
    )
    return rows


def image_status(session: Session, image: Image) -> dict:
    """Single-image version of an :func:`image_overview` row."""
    latest_by_source: dict[str | None, datetime] = {}
    for scan in sorted(image.scans, key=lambda s: s.scanned_at):
        latest_by_source[scan.source] = scan.scanned_at
    return _overview_row(
        image,
        latest_scan(session, image),
        latest_by_source,
        source_watermarks(session),
        container_placements(session),
        k8s_placements(session),
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


def _empty_rollup() -> dict:
    return {
        "images": 0,
        "scanned_images": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "unknown": 0,
        "total": 0,
    }


def vuln_summary(session: Session) -> dict:
    """Rollup over the latest scan per non-noisy image.

    Top-level keys are the fleet-wide totals (backward compatible); the
    ``running`` / ``registry_only`` sub-dicts split the same rollup by
    deployment status.
    """
    out = _empty_rollup()
    out["running"] = _empty_rollup()
    out["registry_only"] = _empty_rollup()
    for row in image_overview(session, include_noisy=False):
        bucket = out["running" if row["status"] == "running" else "registry_only"]
        out["images"] += 1
        bucket["images"] += 1
        scan = row["scan"]
        if scan is None:
            continue
        for b in (out, bucket):
            b["scanned_images"] += 1
            for key in ("critical", "high", "medium", "low", "unknown", "total"):
                b[key] += getattr(scan, key) or 0
    return out
