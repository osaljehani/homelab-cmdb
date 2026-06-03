from fastapi import APIRouter, Request
from cmdb.web.deps import templates

router = APIRouter()


@router.get("/")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "settings",
        "host_count": 0,
        "cluster_count": 0,
        "last_import": None,
        "os_breakdown": {},
    })
