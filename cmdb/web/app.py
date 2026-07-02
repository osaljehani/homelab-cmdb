from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cmdb.web.deps import STATIC_DIR
from cmdb.web.routes import (
    dashboard,
    hosts,
    containers,
    import_,
    collect,
    k8s,
    generate,
    settings,
    images,
    search,
    topology,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from cmdb.db import run_migrations

    run_migrations()
    yield


app = FastAPI(title="HomeLabCMDB", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(dashboard.router)
app.include_router(hosts.router, prefix="/hosts")
app.include_router(containers.router, prefix="/containers")
app.include_router(import_.router, prefix="/import")
app.include_router(collect.router, prefix="/collect")
app.include_router(k8s.router, prefix="/k8s")
app.include_router(generate.router, prefix="/generate")
app.include_router(settings.router, prefix="/settings")
app.include_router(images.router, prefix="/images")
app.include_router(search.router, prefix="/search")
app.include_router(topology.router, prefix="/topology")
