from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cmdb.domain.services.network import network_map
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def network_page(request: Request, db: Session = Depends(get_db_dep)):
    nm = network_map(db)
    return templates.TemplateResponse(
        request,
        "network/index.html",
        {
            "active": "network",
            "map": nm,
            "warning_count": len(nm["duplicate_ips"]) + len(nm["duplicate_macs"]),
        },
    )
