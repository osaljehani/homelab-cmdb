from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from cmdb.web.routes import (
    dashboard,
    hosts,
    containers,
    import_,
    collect,
    k8s,
    generate,
    settings,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield


app = FastAPI(title="HomeLabCMDB", lifespan=lifespan)
app.include_router(dashboard.router)
app.include_router(hosts.router, prefix="/hosts")
app.include_router(containers.router, prefix="/containers")
app.include_router(import_.router, prefix="/import")
app.include_router(collect.router, prefix="/collect")
app.include_router(k8s.router, prefix="/k8s")
app.include_router(generate.router, prefix="/generate")
app.include_router(settings.router, prefix="/settings")
