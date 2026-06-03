from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session
import json

from cmdb.domain.services.hosts import list_hosts, get_host, add_tag, remove_tag
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
        hosts = [h for h in hosts if q_lower in h.hostname.lower()
                 or (h.primary_ipv4 and q_lower in h.primary_ipv4)
                 or (h.os_distribution and q_lower in h.os_distribution.lower())]

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "hosts/_table.html", {"hosts": hosts})
    return templates.TemplateResponse(request, "hosts/list.html", {
        "active": "hosts",
        "hosts": hosts,
        "q": q,
        "tag": tag,
    })


@router.get("/{hostname}")
def host_detail(request: Request, hostname: str, db: Session = Depends(get_db_dep)):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    raw_pretty = json.dumps(host.raw_facts, indent=2) if host.raw_facts else ""
    return templates.TemplateResponse(request, "hosts/detail.html", {
        "active": "hosts",
        "host": host,
        "raw_pretty": raw_pretty,
    })


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
