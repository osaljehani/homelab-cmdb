from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cmdb.domain.services.search import global_search
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def search(request: Request, gq: str = "", db: Session = Depends(get_db_dep)):
    hits = global_search(db, gq)
    is_htmx = request.headers.get("HX-Request") == "true"
    template = "search/_results.html" if is_htmx else "search/results.html"
    return templates.TemplateResponse(
        request,
        template,
        {"active": "", "gq": gq, "hits": hits},
    )
