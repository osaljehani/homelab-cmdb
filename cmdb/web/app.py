from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cmdb.web.deps import STATIC_DIR
from cmdb.web.routes import (
    api,
    dashboard,
    hosts,
    containers,
    import_,
    collect,
    k8s,
    generate,
    settings,
    images,
    network,
    search,
    topology,
)


# Set by create_app() when the remote MCP endpoint is enabled; read by the
# lifespan below. Module-level because uvicorn imports `app`, so the app is
# built at import time and its lifespan runs later.
_mcp_session_manager: object | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    from cmdb.db import run_migrations

    run_migrations()
    if _mcp_session_manager is None:
        yield
        return
    # Load-bearing. Neither Starlette's Mount nor a delegating Route runs a
    # sub-app's lifespan, and streamable_http_app() puts the session manager's
    # task group in exactly that -- so without this every request to /mcp dies
    # with "Task group is not initialized", i.e. a 500 that reads like an app
    # bug rather than a wiring one.
    async with _mcp_session_manager.run():
        yield


def create_app() -> FastAPI:
    """Build the app. A factory because the MCP session manager's run() is
    single-shot, so tests need a fresh instance per case."""
    global _mcp_session_manager

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
    app.include_router(network.router, prefix="/network")
    app.include_router(api.router, prefix="/api/v1", tags=["api"])

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, bool]:
        """Cheap, DB-free liveness probe -- also what static/js/session.js polls
        before letting a mutating form submit.

        MUST stay *behind* the reverse proxy's auth (i.e. never added to
        `skip_path_regex` in authentik/blueprints/60-app-cmdb.yaml). Being
        behind it is the whole point: a lapsed browser session makes this 302
        to the login page, which is the signal session.js reads. Exempting it
        "so monitoring can reach it" would make the probe answer 200 forever
        and the stale-session banner would silently never appear again."""
        return {"ok": True}

    # Appended LAST so every route above wins and unmatched paths still get
    # FastAPI's own 404 rather than the sub-app's. Returns None -- registering
    # nothing at all -- unless CMDB_MCP_REMOTE_ENABLED is set.
    from cmdb.mcp.server import attach_remote_mcp

    _mcp_session_manager = attach_remote_mcp(app)
    return app


app = create_app()
