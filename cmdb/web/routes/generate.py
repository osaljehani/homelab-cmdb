from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from cmdb.domain.services.generate import (
    generate_inventory_ini, generate_inventory_yaml, generate_ssh_config
)
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def generate_page(
    request: Request,
    tag: str = "",
    db: Session = Depends(get_db_dep),
):
    inv_yaml = generate_inventory_yaml(db, tag=tag or None)
    inv_ini = generate_inventory_ini(db, tag=tag or None)
    ssh = generate_ssh_config(db, tag=tag or None)
    return templates.TemplateResponse(request, "generate/index.html", {
        "active": "generate",
        "inv_yaml": inv_yaml,
        "inv_ini": inv_ini,
        "ssh_config": ssh,
        "tag": tag,
    })


@router.get("/download/inventory.yaml")
def download_inventory_yaml(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_inventory_yaml(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=inventory.yaml"})


@router.get("/download/inventory.ini")
def download_inventory_ini(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_inventory_ini(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=hosts.ini"})


@router.get("/download/ssh-config")
def download_ssh_config(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_ssh_config(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=ssh-config"})


@router.get("/download/cmdb.json")
def download_cmdb_export(db: Session = Depends(get_db_dep)):
    import json

    from cmdb.domain.services.export import export_all

    content = json.dumps(export_all(db), indent=2)
    return Response(content, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=cmdb.json"})
