from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cmdb.domain.models import Host
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def containers_page(request: Request, db: Session = Depends(get_db_dep)):
    # Hosts that have at least one container, with their containers.
    hosts = db.query(Host).order_by(Host.hostname).all()
    hosts_with_containers = [h for h in hosts if h.containers]
    total = sum(len(h.containers) for h in hosts_with_containers)
    return templates.TemplateResponse(
        request,
        "containers/index.html",
        {
            "active": "containers",
            "hosts": hosts_with_containers,
            "total": total,
        },
    )
