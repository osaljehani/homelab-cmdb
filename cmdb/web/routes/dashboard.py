from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from cmdb.config import settings
from cmdb.domain.models import Host, K8sCluster, Container, ImportLog
from cmdb.domain.services.dashboard import (
    fleet_freshness,
    os_breakdown,
    recent_changes,
    vuln_trend,
)
from cmdb.domain.services.security import posture_summary
from cmdb.domain.services.images import running_image_ids, vuln_summary
from cmdb.domain.services.network import network_map
from cmdb.domain.services.storage import fleet_storage
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db_dep)):
    hosts = db.query(Host).order_by(Host.hostname).all()
    cluster_count = db.query(func.count(K8sCluster.id)).scalar()
    container_count = db.query(func.count(Container.id)).scalar()
    last_import = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).first()
    nm = network_map(db)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "host_count": len(hosts),
            "cluster_count": cluster_count,
            "container_count": container_count,
            "last_import": last_import,
            "security": posture_summary(hosts),
            "vuln_summary": vuln_summary(db),
            "vuln_trend": vuln_trend(db, image_ids=running_image_ids(db)),
            "freshness": fleet_freshness(db, stale_days=settings.stale_days),
            "changes": recent_changes(db),
            "os_breakdown": os_breakdown(db),
            "stale_days": settings.stale_days,
            "network_subnets": len(nm["subnets"]),
            "network_warnings": len(nm["duplicate_ips"]) + len(nm["duplicate_macs"]),
            "storage_warnings": fleet_storage(
                db, warn_pct=settings.storage_warn_pct
            )["warnings"],
            "storage_warn_pct": settings.storage_warn_pct,
        },
    )
