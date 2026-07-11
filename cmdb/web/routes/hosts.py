from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session
import json

from cmdb.domain.services.hosts import (
    add_tag,
    get_host,
    list_hosts,
    remove_custom_field,
    remove_tag,
    set_custom_field,
    set_notes,
)
from cmdb.config import settings
from cmdb.domain.services.history import host_history
from cmdb.domain.services.storage import host_storage
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def hosts_list(
    request: Request,
    q: str = "",
    tag: str = "",
    db: Session = Depends(get_db_dep),
):
    hosts = list_hosts(db, tag=tag or None, os_family=None)
    if q:
        q_lower = q.lower()
        hosts = [
            h
            for h in hosts
            if q_lower in h.hostname.lower()
            or (h.primary_ipv4 and q_lower in h.primary_ipv4)
            or (h.os_distribution and q_lower in h.os_distribution.lower())
        ]

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request, "hosts/_table.html", {"hosts": hosts}
        )
    return templates.TemplateResponse(
        request,
        "hosts/list.html",
        {
            "active": "hosts",
            "hosts": hosts,
            "q": q,
            "tag": tag,
        },
    )


@router.get("/{hostname}")
def host_detail(request: Request, hostname: str, db: Session = Depends(get_db_dep)):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    raw_pretty = json.dumps(host.raw_facts, indent=2) if host.raw_facts else ""
    return templates.TemplateResponse(
        request,
        "hosts/detail.html",
        {
            "active": "hosts",
            "host": host,
            "raw_pretty": raw_pretty,
            "history": host_history(db, host),
            "storage": host_storage(host),
            "storage_warn_pct": settings.storage_warn_pct,
        },
    )


@router.post("/{hostname}/tags")
def host_add_tag(
    request: Request,
    hostname: str,
    tag: str = Form(...),
    db: Session = Depends(get_db_dep),
):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404)
    add_tag(db, hostname, tag)
    host = get_host(db, hostname)
    return templates.TemplateResponse(request, "hosts/_tag_list.html", {"host": host})


@router.delete("/{hostname}/tags/{tag_name}")
def host_remove_tag(
    request: Request,
    hostname: str,
    tag_name: str,
    db: Session = Depends(get_db_dep),
):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404)
    remove_tag(db, hostname, tag_name)
    host = get_host(db, hostname)
    return templates.TemplateResponse(request, "hosts/_tag_list.html", {"host": host})


@router.post("/{hostname}/notes")
def host_set_notes(
    request: Request,
    hostname: str,
    notes: str = Form(""),
    db: Session = Depends(get_db_dep),
):
    try:
        host = set_notes(db, hostname, notes)
    except ValueError:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "hosts/_notes.html", {"host": host, "saved": True}
    )


@router.post("/{hostname}/fields")
def host_set_field(
    request: Request,
    hostname: str,
    key: str = Form(...),
    value: str = Form(""),
    db: Session = Depends(get_db_dep),
):
    if not key.strip():
        host = get_host(db, hostname)
        if not host:
            raise HTTPException(status_code=404)
    else:
        try:
            host = set_custom_field(db, hostname, key, value)
        except ValueError:
            raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "hosts/_custom_fields.html", {"host": host}
    )


@router.delete("/{hostname}/fields/{key}")
def host_remove_field(
    request: Request,
    hostname: str,
    key: str,
    db: Session = Depends(get_db_dep),
):
    try:
        host = remove_custom_field(db, hostname, key)
    except ValueError:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "hosts/_custom_fields.html", {"host": host}
    )
