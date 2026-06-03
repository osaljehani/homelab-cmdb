import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from cmdb.domain.models import ImportLog, ImportSource
from cmdb.domain.services.ansible import import_from_path
from cmdb.domain.services.k8s_import import import_from_path as k8s_import_from_path
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def import_page(request: Request, db: Session = Depends(get_db_dep)):
    logs = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all()
    return templates.TemplateResponse(request, "import/index.html", {
        "active": "import",
        "logs": logs,
    })


@router.post("/upload")
async def import_upload(
    request: Request,
    db: Session = Depends(get_db_dep),
    files: list[UploadFile] = File(...),
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for f in files:
            dest = tmp_path / (Path(f.filename).name if f.filename else "upload.json")
            dest.write_bytes(await f.read())
        log = import_from_path(db, tmp, ImportSource.WEB)

    return templates.TemplateResponse(request, "import/index.html", {
        "active": "import",
        "logs": db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all(),
        "last_result": log,
        "last_result_type": "ansible",
    })


@router.post("/upload/k8s")
async def import_k8s_upload(
    request: Request,
    db: Session = Depends(get_db_dep),
    files: list[UploadFile] = File(...),
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for f in files:
            dest = tmp_path / (Path(f.filename).name if f.filename else "upload.json")
            dest.write_bytes(await f.read())
        log = k8s_import_from_path(db, tmp, ImportSource.WEB)

    return templates.TemplateResponse(request, "import/index.html", {
        "active": "import",
        "logs": db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all(),
        "last_result": log,
        "last_result_type": "k8s",
    })
