"""Dashboard CVE metrics: longer-window trend deltas, unique CVEs, worst packages.

Scope matches ``vuln_summary(...)["running"]``: running, non-noisy images, the
latest scan per image — resolved by :func:`image_overview`. The trend deltas
read the same daily snapshot totals the sparkline plots (instance counts), the
other two metrics read per-CVE rows from the latest scans. All functions take a
Session and an injectable ``now`` where time matters; datetimes are naive UTC.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from cmdb.domain.models import Vulnerability
from cmdb.domain.services.images import image_overview
from cmdb.domain.services.vuln_snapshots import snapshot_trend

SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
_RANK = {sev: i for i, sev in enumerate(SEVERITY_ORDER)}


def severity_rank(severity: str | None) -> int:
    """0 for CRITICAL … 4 for UNKNOWN; unrecognised/missing counts as UNKNOWN."""
    return _RANK.get((severity or "UNKNOWN").upper(), _RANK["UNKNOWN"])


def trend_deltas(
    session: Session, windows=(30, 90), now: datetime | None = None
) -> list[dict]:
    """First-vs-last snapshot total per window.

    ``pct`` is None with fewer than two points or a zero start total; ``delta``
    is None only with fewer than two points.
    """
    out = []
    for days in windows:
        points = snapshot_trend(session, days=days, now=now)
        row = {
            "days": days,
            "start_date": points[0]["date"] if points else None,
            "start_total": points[0]["total"] if points else None,
            "end_total": points[-1]["total"] if points else None,
            "delta": None,
            "pct": None,
        }
        if len(points) >= 2:
            row["delta"] = row["end_total"] - row["start_total"]
            if row["start_total"]:
                row["pct"] = round(row["delta"] / row["start_total"] * 100, 1)
        out.append(row)
    return out


def _running_rows(session: Session) -> list[dict]:
    return [
        row
        for row in image_overview(session, include_noisy=False)
        if row["status"] == "running" and row["scan"] is not None
    ]


def _vulns_by_scan(session: Session, scan_ids: list[int]) -> dict[int, list]:
    by_scan: dict[int, list] = {sid: [] for sid in scan_ids}
    if not scan_ids:
        return by_scan
    for v in session.query(Vulnerability).filter(Vulnerability.scan_id.in_(scan_ids)):
        by_scan[v.scan_id].append(v)
    return by_scan


def unique_cves_by_severity(session: Session) -> dict:
    """Distinct ``vuln_id`` counts by severity across the in-scope latest scans.

    A CVE reported with different severities (per image or package) counts
    once, under its highest severity.
    """
    scan_ids = [row["scan"].id for row in _running_rows(session)]
    best: dict[str, int] = {}
    for vulns in _vulns_by_scan(session, scan_ids).values():
        for v in vulns:
            rank = severity_rank(v.severity)
            best[v.vuln_id] = min(rank, best.get(v.vuln_id, rank))
    out = {sev.lower(): 0 for sev in SEVERITY_ORDER}
    for rank in best.values():
        out[SEVERITY_ORDER[rank].lower()] += 1
    out["total"] = len(best)
    return out


def _host_labels(deployments: dict) -> set[tuple[str, str]]:
    # K8sWorkload carries no node, so a cluster is the finest k8s grain.
    labels = {("docker", d["host"]) for d in deployments["docker"] if d["host"]}
    labels |= {("k8s", k["cluster"]) for k in deployments["kubernetes"] if k["cluster"]}
    return labels


def top_risk_packages(session: Session) -> list[dict]:
    """The worst package per Docker host / k8s cluster, by CRITICAL then HIGH.

    Per host, the CRITICAL/HIGH findings of its images are grouped by package
    and counted as distinct CVE IDs; the top package wins on (crit, high,
    name). ``images`` lists that host's refs carrying the package with a
    crit/high finding. Hosts without crit/high findings are omitted; rows are
    sorted crit desc, high desc, then host.
    """
    rows = _running_rows(session)
    by_scan = _vulns_by_scan(session, [row["scan"].id for row in rows])

    # (kind, host) -> pkg -> {"crit": {cve}, "high": {cve}, "images": {ref}}
    hosts: dict[tuple[str, str], dict[str, dict]] = {}
    for row in rows:
        ref = row["image"].ref
        findings = [
            v
            for v in by_scan[row["scan"].id]
            if severity_rank(v.severity) <= _RANK["HIGH"]
        ]
        if not findings:
            continue
        for label in _host_labels(row["deployments"]):
            pkgs = hosts.setdefault(label, {})
            for v in findings:
                entry = pkgs.setdefault(
                    v.pkg_name or "(unknown)",
                    {"crit": set(), "high": set(), "images": set()},
                )
                bucket = "crit" if severity_rank(v.severity) == 0 else "high"
                entry[bucket].add(v.vuln_id)
                entry["images"].add(ref)

    out = []
    for (kind, host), pkgs in hosts.items():
        # A CVE seen as both CRITICAL and HIGH counts once, as critical.
        counts = {
            pkg: (len(e["crit"]), len(e["high"] - e["crit"])) for pkg, e in pkgs.items()
        }
        package = min(counts, key=lambda pkg: (-counts[pkg][0], -counts[pkg][1], pkg))
        crit, high = counts[package]
        out.append(
            {
                "host": host,
                "kind": kind,
                "package": package,
                "crit": crit,
                "high": high,
                "images": sorted(pkgs[package]["images"]),
            }
        )
    out.sort(key=lambda r: (-r["crit"], -r["high"], r["host"]))
    return out
