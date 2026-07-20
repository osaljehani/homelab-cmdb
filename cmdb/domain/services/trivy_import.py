import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from cmdb.domain.models import (
    Image,
    ImageScan,
    ImportLog,
    ImportSource,
    Vulnerability,
)
from cmdb.domain.refs import canonical_ref

_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}

# trivy_version values emitted by registry (zot) scans; runtime Docker scans use
# an actual trivy release string (e.g. "0.72.0").
_REGISTRY_TRIVY_VERSIONS = {"zot-embedded", "registry-embedded"}


def _derive_source(host: Any, trivy_version: Any) -> str:
    """Classify a scan run as "docker" or "kubernetes" from its envelope.

    The zot registry scan covers containerd/k8s-pulled images (host
    ``zot-registry``, trivy_version ``zot-embedded``); the runtime scan covers
    the Docker daemon. Defaults to "docker" when nothing signals otherwise.
    """
    if trivy_version in _REGISTRY_TRIVY_VERSIONS:
        return "kubernetes"
    host_str = str(host or "").lower()
    if host_str == "zot-registry" or "zot" in host_str or "registry" in host_str:
        return "kubernetes"
    return "docker"


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp (accepting a trailing 'Z') to naive UTC."""
    if not value:
        return datetime.utcnow()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.utcnow()
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _ingest_report(
    session: Session,
    report: dict[str, Any],
    scanned_at: datetime,
    trivy_version: str | None,
    source: str | None,
    host: str | None,
    import_log_id: int | None,
) -> int:
    """Ingest one trivy `image --format json` report. Returns the vuln count."""
    ref = report.get("ArtifactName")
    if not ref:
        raise ValueError("report missing 'ArtifactName'")
    ref = canonical_ref(ref)

    metadata = report.get("Metadata") or {}
    digests = metadata.get("RepoDigests") or []
    digest = digests[0] if digests else metadata.get("ImageID")

    image = session.query(Image).filter(Image.ref == ref).first()
    if image is None:
        image = Image(ref=ref, first_seen=scanned_at)
        session.add(image)
        session.flush()
    image.digest = digest
    image.last_scanned_at = scanned_at

    scan = ImageScan(
        image_id=image.id,
        scanned_at=scanned_at,
        trivy_version=trivy_version,
        source=source,
        host=host,
        import_log_id=import_log_id,
    )
    session.add(scan)
    session.flush()

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    total = 0
    for result in report.get("Results") or []:
        target = result.get("Target")
        for v in result.get("Vulnerabilities") or []:
            sev = (v.get("Severity") or "UNKNOWN").upper()
            bucket = sev.lower() if sev in _SEVERITIES else "unknown"
            counts[bucket] += 1
            total += 1
            session.add(
                Vulnerability(
                    scan_id=scan.id,
                    vuln_id=v.get("VulnerabilityID") or "",
                    pkg_name=v.get("PkgName"),
                    installed_version=v.get("InstalledVersion"),
                    fixed_version=v.get("FixedVersion"),
                    severity=sev,
                    title=v.get("Title"),
                    target=target,
                )
            )

    scan.critical = counts["critical"]
    scan.high = counts["high"]
    scan.medium = counts["medium"]
    scan.low = counts["low"]
    scan.unknown = counts["unknown"]
    scan.total = total
    session.flush()
    return total


def import_scan_run(
    session: Session, envelope: dict[str, Any], import_log_id: int | None = None
) -> dict[str, Any]:
    """Ingest one scan-run envelope: {host, scanned_at, trivy_version, images:[...]}."""
    images = envelope.get("images")
    if images is None:
        raise ValueError("'images' key is required")

    scanned_at = _parse_ts(envelope.get("scanned_at"))
    trivy_version = envelope.get("trivy_version")
    host = envelope.get("host")
    # Prefer an explicit envelope "source" if the scanner declares one; otherwise
    # derive docker-vs-kubernetes from the host label / trivy_version.
    source = envelope.get("source") or _derive_source(host, trivy_version)

    imgs = 0
    vulns = 0
    errors: list[str] = []
    for i, report in enumerate(images):
        try:
            vulns += _ingest_report(
                session, report, scanned_at, trivy_version, source, host, import_log_id
            )
            imgs += 1
        except Exception as exc:  # noqa: BLE001 - collected, non-fatal
            errors.append(f"image[{i}]: {exc}")
    return {"images": imgs, "vulnerabilities": vulns, "errors": errors}


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = (
        [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]
    )

    log = ImportLog(
        source=source,
        filename=str(path),
        hosts_upserted=0,
        hosts_failed=0,
        images_scanned=0,
        vulnerabilities_upserted=0,
    )
    session.add(log)
    session.flush()  # need log.id for scan.import_log_id

    total_imgs = 0
    total_vulns = 0
    all_errors: list[str] = []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception as exc:  # noqa: BLE001
            all_errors.append(f"{f.name}: JSON parse error: {exc}")
            continue
        envelopes = data if isinstance(data, list) else [data]
        for j, env in enumerate(envelopes):
            label = f"{f.name}[{j}]" if j else f.name
            try:
                counts = import_scan_run(session, env, import_log_id=log.id)
                total_imgs += counts["images"]
                total_vulns += counts["vulnerabilities"]
                all_errors.extend(f"{label}: {e}" for e in counts["errors"])
            except Exception as exc:  # noqa: BLE001
                all_errors.append(f"{label}: {exc}")

    log.images_scanned = total_imgs
    log.vulnerabilities_upserted = total_vulns
    log.notes = "\n".join(all_errors) or None

    # Freeze today's per-image rollups + running/noisy flags for the trend.
    # Local import: vuln_snapshots pulls image_overview from the images
    # service, which imports this module's tables — avoid the cycle.
    from cmdb.domain.services.vuln_snapshots import write_daily_snapshot

    write_daily_snapshot(session)
    session.flush()
    return log
