import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.models import ImportLog, ImportSource
from cmdb.domain.services.collect import (
    CollectError,
    collect_docker,
    collect_facts,
    collect_k8s,
)
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


def _recent_logs(db: Session) -> list[ImportLog]:
    return db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all()


@router.get("/")
def collect_page(request: Request, db: Session = Depends(get_db_dep)):
    return templates.TemplateResponse(
        request,
        "collect/index.html",
        {
            "active": "collect",
            "inventory": settings.ansible_inventory,
            "logs": _recent_logs(db),
        },
    )


@router.post("/run")
async def collect_run(
    request: Request,
    mode: str = Form("all"),
    limit: str = Form(""),
    inventory: UploadFile | None = File(None),
    db: Session = Depends(get_db_dep),
):
    limit_val = limit.strip() or None
    error: str | None = None
    last_result: ImportLog | None = None
    last_result_type = mode

    # An uploaded inventory is used for this run only; otherwise pass None so the
    # collect service falls back to env var / DB-generated inventory.
    inv_tmp: str | None = None
    if inventory is not None:
        content = await inventory.read()
        if content.strip():
            tmp = tempfile.NamedTemporaryFile(
                "wb", suffix=".yml", prefix="cmdb-upload-", delete=False
            )
            tmp.write(content)
            tmp.close()
            inv_tmp = tmp.name

    try:
        if mode == "facts":
            last_result = collect_facts(db, inv_tmp, limit_val, ImportSource.COLLECT)
        elif mode == "docker":
            last_result = collect_docker(db, inv_tmp, limit_val, ImportSource.COLLECT)
        elif mode == "k8s":
            last_result = collect_k8s(db, inv_tmp, limit_val, ImportSource.COLLECT)
        else:  # all   facts + docker + k8s; surface the docker log, others merge in
            facts_log = collect_facts(db, inv_tmp, limit_val, ImportSource.COLLECT)
            last_result = collect_docker(db, inv_tmp, limit_val, ImportSource.COLLECT)
            k8s_log = collect_k8s(db, inv_tmp, limit_val, ImportSource.COLLECT)
            last_result.hosts_upserted = facts_log.hosts_upserted
            last_result.hosts_failed = facts_log.hosts_failed
            last_result.k8s_clusters_upserted = k8s_log.k8s_clusters_upserted
            last_result.k8s_nodes_upserted = k8s_log.k8s_nodes_upserted
            last_result.k8s_namespaces_upserted = k8s_log.k8s_namespaces_upserted
            merged_notes = [
                n for n in (facts_log.notes, last_result.notes, k8s_log.notes) if n
            ]
            last_result.notes = "\n".join(merged_notes) if merged_notes else None
            last_result_type = "all"
    except CollectError as exc:
        error = str(exc)
    finally:
        if inv_tmp is not None:
            Path(inv_tmp).unlink(missing_ok=True)

    return templates.TemplateResponse(
        request,
        "collect/index.html",
        {
            "active": "collect",
            "inventory": settings.ansible_inventory,
            "logs": _recent_logs(db),
            "last_result": last_result,
            "last_result_type": last_result_type,
            "error": error,
        },
    )
