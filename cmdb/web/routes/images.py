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
    deployed: str | None = None,
    deleted: str | None = None,
    scans: int | None = None,
    vulns: int | None = None,
):
    rows = images_svc.image_overview(db)
    counts = {
        "all": len(rows),
        "running": sum(1 for r in rows if r["status"] == "running"),
        "registry-only": sum(1 for r in rows if r["status"] == "registry-only"),
    }
    if deployed in ("running", "registry-only"):
        rows = [r for r in rows if r["status"] == deployed]
    return templates.TemplateResponse(
        request,
        "images/list.html",
        {
            "active": "images",
            "rows": rows,
            "deployed": deployed or "all",
            "counts": counts,
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
    status = images_svc.image_status(db, image)
    vuln_count = sum(len(scan.vulnerabilities) for scan in image.scans)
    return templates.TemplateResponse(
        request,
        "images/detail.html",
        {
            "active": "images",
            "image": image,
            "latest": status["scan"],
            "scans": image.scans,  # already ordered scanned_at desc
            "stale": status["stale"],
            "deployments": status["deployments"],
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
