from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.models import ImportLog, ImportSource
from cmdb.domain.services.collect import (
    CollectError,
    collect_docker,
    collect_facts,
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
def collect_run(
    request: Request,
    mode: str = Form("all"),
    limit: str = Form(""),
    db: Session = Depends(get_db_dep),
):
    limit_val = limit.strip() or None
    error: str | None = None
    last_result: ImportLog | None = None
    last_result_type = mode

    try:
        if mode == "facts":
            last_result = collect_facts(db, None, limit_val, ImportSource.COLLECT)
        elif mode == "docker":
            last_result = collect_docker(db, None, limit_val, ImportSource.COLLECT)
        else:  # all   facts then docker; surface the docker log, facts errors merge in
            facts_log = collect_facts(db, None, limit_val, ImportSource.COLLECT)
            last_result = collect_docker(db, None, limit_val, ImportSource.COLLECT)
            last_result.hosts_upserted = facts_log.hosts_upserted
            last_result.hosts_failed = facts_log.hosts_failed
            if facts_log.notes:
                last_result.notes = "\n".join(
                    n for n in (facts_log.notes, last_result.notes) if n
                )
            last_result_type = "all"
    except CollectError as exc:
        error = str(exc)

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
