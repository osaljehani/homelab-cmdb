from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.domain.models import K8sNodeRole
from cmdb.domain.services.hosts import list_hosts
from cmdb.domain.services.k8s import (
    add_cluster, add_node, delete_cluster,
    list_clusters, remove_node,
)
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def k8s_page(request: Request, db: Session = Depends(get_db_dep)):
    clusters = list_clusters(db)
    all_hosts = list_hosts(db)
    return templates.TemplateResponse(request, "k8s/index.html", {
        "active": "k8s",
        "clusters": clusters,
        "all_hosts": all_hosts,
        "roles": [r.value for r in K8sNodeRole],
    })


@router.post("/clusters")
def create_cluster(
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db_dep),
):
    add_cluster(db, name, description or None)
    return RedirectResponse("/k8s", status_code=303)


@router.post("/clusters/{name}/delete")
def delete_cluster_route(name: str, db: Session = Depends(get_db_dep)):
    delete_cluster(db, name)
    return RedirectResponse("/k8s", status_code=303)


@router.post("/nodes")
def add_node_route(
    hostname: str = Form(...),
    cluster: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db_dep),
):
    add_node(db, hostname, cluster, K8sNodeRole(role))
    return RedirectResponse("/k8s", status_code=303)


@router.post("/nodes/{hostname}/{cluster}/delete")
def remove_node_route(hostname: str, cluster: str, db: Session = Depends(get_db_dep)):
    remove_node(db, hostname, cluster)
    return RedirectResponse("/k8s", status_code=303)
