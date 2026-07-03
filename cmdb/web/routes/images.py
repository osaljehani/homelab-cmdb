from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.domain.services import images as images_svc
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def images_page(
    request: Request,
    db: Session = Depends(get_db_dep),
    deleted: str | None = None,
    scans: int | None = None,
    vulns: int | None = None,
):
    newest = images_svc.newest_scan_time(db)
    rows = []
    for image in images_svc.list_images(db):
        rows.append(
            {
                "image": image,
                "scan": images_svc.latest_scan(db, image),
                "stale": images_svc.is_stale(image, newest),
                "deployments": images_svc.deployments(db, image),
            }
        )
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
        {
            "active": "images",
            "rows": rows,
            "deleted": deleted,
            "deleted_scans": scans,
            "deleted_vulns": vulns,
        },
    )


@router.get("/{ref:path}")
def image_detail(ref: str, request: Request, db: Session = Depends(get_db_dep)):
    image = images_svc.get_image(db, ref)
    if image is None:
        raise HTTPException(status_code=404, detail=f"Image '{ref}' not found")
    latest = images_svc.latest_scan(db, image)
    newest = images_svc.newest_scan_time(db)
    vuln_count = sum(len(scan.vulnerabilities) for scan in image.scans)
    return templates.TemplateResponse(
        request,
        "images/detail.html",
        {
            "active": "images",
            "image": image,
            "latest": latest,
            "scans": image.scans,  # already ordered scanned_at desc
            "stale": images_svc.is_stale(image, newest),
            "deployments": images_svc.deployments(db, image),
            "vuln_count": vuln_count,
        },
    )


@router.post("/{ref:path}/noisy")
def image_toggle_noisy(
    ref: str, db: Session = Depends(get_db_dep), on: bool = Form(False)
):
    images_svc.set_noisy(db, ref, on)
    return RedirectResponse(url=f"/images/{ref}", status_code=303)


@router.post("/{ref:path}/delete")
def image_delete(ref: str, db: Session = Depends(get_db_dep)):
    try:
        result = images_svc.delete_image(db, ref)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Image '{ref}' not found")
    url = (
        f"/images/?deleted={result['ref']}"
        f"&scans={result['scans']}&vulns={result['vulnerabilities']}"
    )
    return RedirectResponse(url=url, status_code=303)
