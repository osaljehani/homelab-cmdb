"""Read-only JSON API (/api/v1) — thin GET wrappers over the domain services.

Same serialization models as the MCP server (cmdb/domain/schemas.py), so the
two machine-facing surfaces stay in lockstep. No auth, like the rest of the
app: keep the port LAN/tailnet-only.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from cmdb.domain.models import Container
from cmdb.domain.schemas import (
    ContainerWithHostOut,
    HostDetailOut,
    HostOut,
    ImageDetailOut,
    ImageSummaryOut,
    K8sClusterOut,
    VulnSummaryOut,
    image_summary_out,
    running_on,
)
from cmdb.domain.services import hosts as hosts_svc
from cmdb.domain.services import images as images_svc
from cmdb.domain.services import k8s as k8s_svc
from cmdb.web.deps import get_db_dep

router = APIRouter()


@router.get("/hosts", response_model=list[HostOut])
def api_hosts(
    tag: str | None = None,
    os_family: str | None = None,
    db: Session = Depends(get_db_dep),
):
    return hosts_svc.list_hosts(db, tag=tag, os_family=os_family)


@router.get("/hosts/{hostname}", response_model=HostDetailOut)
def api_host_detail(hostname: str, db: Session = Depends(get_db_dep)):
    host = hosts_svc.get_host(db, hostname)
    if host is None:
        raise HTTPException(status_code=404, detail=f"Host '{hostname}' not found")
    return host


@router.get("/containers", response_model=list[ContainerWithHostOut])
def api_containers(db: Session = Depends(get_db_dep)):
    rows = db.query(Container).order_by(Container.name).all()
    return [ContainerWithHostOut.model_validate(c) for c in rows]


@router.get("/clusters", response_model=list[K8sClusterOut])
def api_clusters(db: Session = Depends(get_db_dep)):
    return [
        K8sClusterOut(
            name=c.name,
            description=c.description,
            node_count=len(c.nodes),
            namespaces=[ns.name for ns in c.namespaces],
        )
        for c in k8s_svc.list_clusters(db)
    ]


@router.get("/vuln-summary", response_model=VulnSummaryOut)
def api_vuln_summary(db: Session = Depends(get_db_dep)):
    return VulnSummaryOut(**images_svc.vuln_summary(db))


@router.get("/images", response_model=list[ImageSummaryOut])
def api_images(db: Session = Depends(get_db_dep)):
    return [
        image_summary_out(db, row["image"], row)
        for row in images_svc.image_overview(db)
    ]


@router.get("/images/{ref:path}", response_model=ImageDetailOut)
def api_image_detail(ref: str, db: Session = Depends(get_db_dep)):
    image = images_svc.get_image(db, ref)
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image '{ref}' not found")
    row = images_svc.image_status(db, image)
    scan = row["scan"]
    return ImageDetailOut(
        ref=image.ref,
        expected_noisy=image.expected_noisy,
        scanned_at=scan.scanned_at if scan else None,
        trivy_version=scan.trivy_version if scan else None,
        stale=row["stale"],
        deployment_status=row["status"],
        running_on=running_on(row),
        vulnerabilities=list(scan.vulnerabilities) if scan else [],
    )
