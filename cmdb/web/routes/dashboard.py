from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from cmdb.domain.models import Host, K8sCluster, Container, ImportLog
from cmdb.domain.services.security import posture_summary
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db_dep)):
    hosts = db.query(Host).order_by(Host.hostname).all()
    host_count = len(hosts)
    cluster_count = db.query(func.count(K8sCluster.id)).scalar()
    container_count = db.query(func.count(Container.id)).scalar()
    last_import = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).first()
    security = posture_summary(hosts)

    os_breakdown: dict[str, int] = {}
    for family, count in (
        db.query(Host.os_family, func.count(Host.id)).group_by(Host.os_family).all()
    ):
        os_breakdown[family or "Unknown"] = count

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "host_count": host_count,
            "cluster_count": cluster_count,
            "container_count": container_count,
            "last_import": last_import,
            "os_breakdown": os_breakdown,
            "security": security,
        },
    )
