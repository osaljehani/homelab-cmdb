from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
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


@router.get("/{ref:path}")
def image_detail(ref: str, request: Request, db: Session = Depends(get_db_dep)):
    image = images_svc.get_image(db, ref)
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image '{ref}' not found")
    latest = images_svc.latest_scan(db, image)
    return templates.TemplateResponse(
        request,
        "images/detail.html",
        {
            "active": "images",
            "image": image,
            "latest": latest,
            "scans": image.scans,  # already ordered scanned_at desc
        },
    )


@router.post("/{ref:path}/noisy")
def image_toggle_noisy(
    ref: str, db: Session = Depends(get_db_dep), on: bool = Form(False)
):
    images_svc.set_noisy(db, ref, on)
    return RedirectResponse(url=f"/images/{ref}", status_code=303)
