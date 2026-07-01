from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cmdb.domain.services import images as images_svc
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def images_page(request: Request, db: Session = Depends(get_db_dep)):
    rows = []
    for image in images_svc.list_images(db):
        rows.append({"image": image, "scan": images_svc.latest_scan(db, image)})
    # Non-noisy first, then by descending latest-critical count.
    rows.sort(
        key=lambda r: (
            r["image"].expected_noisy,
            -(r["scan"].critical if r["scan"] else 0),
        )
    )
    return templates.TemplateResponse(
        request,
        "images/list.html",
        {"active": "images", "rows": rows},
    )
