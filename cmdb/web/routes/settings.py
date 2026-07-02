from fastapi import APIRouter, Request

from cmdb.config import settings
from cmdb.web.deps import templates

router = APIRouter()


@router.get("/")
def settings_page(request: Request):
    return templates.TemplateResponse(
        request,
        "settings/index.html",
        {"active": "settings", "settings": settings},
    )
