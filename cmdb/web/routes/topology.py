from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from cmdb.domain.services.topology import build_topology
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def topology_page(request: Request):
    return templates.TemplateResponse(
        request, "topology/index.html", {"active": "topology"}
    )


@router.get("/data")
def topology_data(db: Session = Depends(get_db_dep)):
    return JSONResponse(build_topology(db))
